// Native monitors for Double Battle and Record Start/Stop using WIC + simple matching
// Build (MinGW):
//   g++ -std=c++17 -O2 -shared -o build/automation.dll automation.cpp -lole32 -loleaut32 -lwindowscodecs

#include <windows.h>
#include <wincodec.h>
#include <objbase.h>

#include <atomic>
#include <cwchar>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include <mutex>

struct ComInit {
    bool ok;
    ComInit() {
        HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        ok = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;
    }
    ~ComInit() {
        if (ok) CoUninitialize();
    }
};

struct WicFactory {
    IWICImagingFactory* fac {nullptr};
    WicFactory() {
        CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&fac));
    }
    ~WicFactory() { if (fac) fac->Release(); }
    bool ok() const { return fac != nullptr; }
};

static bool load_image_bgra(IWICImagingFactory* fac, const wchar_t* path, std::vector<unsigned char>& out, UINT& w, UINT& h) {
    if (!fac || !path) return false;
    IWICBitmapDecoder* dec = nullptr;
    HRESULT hr = fac->CreateDecoderFromFilename(path, nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &dec);
    if (FAILED(hr) || !dec) return false;
    IWICBitmapFrameDecode* frame = nullptr;
    hr = dec->GetFrame(0, &frame);
    if (FAILED(hr) || !frame) { dec->Release(); return false; }
    frame->GetSize(&w, &h);
    if (w == 0 || h == 0) { frame->Release(); dec->Release(); return false; }
    IWICFormatConverter* conv = nullptr;
    hr = fac->CreateFormatConverter(&conv);
    if (FAILED(hr) || !conv) { frame->Release(); dec->Release(); return false; }
    hr = conv->Initialize(frame, GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) { conv->Release(); frame->Release(); dec->Release(); return false; }
    const UINT stride = w * 4;
    out.resize((size_t)stride * h);
    WICRect rc {0, 0, (INT)w, (INT)h};
    hr = conv->CopyPixels(&rc, stride, (UINT)out.size(), out.data());
    conv->Release(); frame->Release(); dec->Release();
    return SUCCEEDED(hr);
}

static bool save_image_bgra(IWICImagingFactory* fac, const wchar_t* path, const unsigned char* data, UINT w, UINT h) {
    if (!fac || !path || !data || w == 0 || h == 0) return false;
    IWICStream* stream = nullptr;
    HRESULT hr = fac->CreateStream(&stream);
    if (FAILED(hr) || !stream) return false;
    hr = stream->InitializeFromFilename(path, GENERIC_WRITE);
    if (FAILED(hr)) { stream->Release(); return false; }
    GUID container = GUID_ContainerFormatPng;
    const wchar_t* dot = wcsrchr(path, L'.');
    if (dot) {
        if (_wcsicmp(dot, L".jpg") == 0 || _wcsicmp(dot, L".jpeg") == 0) container = GUID_ContainerFormatJpeg;
        else if (_wcsicmp(dot, L".png") == 0) container = GUID_ContainerFormatPng;
    }
    IWICBitmapEncoder* enc = nullptr;
    hr = fac->CreateEncoder(container, nullptr, &enc);
    if (FAILED(hr) || !enc) { stream->Release(); return false; }
    hr = enc->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) { enc->Release(); stream->Release(); return false; }
    IWICBitmapFrameEncode* frame = nullptr; IPropertyBag2* props = nullptr;
    hr = enc->CreateNewFrame(&frame, &props);
    if (FAILED(hr) || !frame) { if (props) props->Release(); enc->Release(); stream->Release(); return false; }
    if (container == GUID_ContainerFormatJpeg && props) {
        PROPBAG2 opt{}; opt.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
        VARIANT var; VariantInit(&var); var.vt = VT_R4; var.fltVal = 0.9f; props->Write(1, &opt, &var); VariantClear(&var);
    }
    hr = frame->Initialize(props);
    if (FAILED(hr)) { if (props) props->Release(); frame->Release(); enc->Release(); stream->Release(); return false; }
    hr = frame->SetSize(w, h);
    if (FAILED(hr)) { if (props) props->Release(); frame->Release(); enc->Release(); stream->Release(); return false; }
    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
    hr = frame->SetPixelFormat(&fmt);
    if (FAILED(hr)) { if (props) props->Release(); frame->Release(); enc->Release(); stream->Release(); return false; }
    // WriteSource needs an IWICBitmapSource; we can use a bitmap created from memory
    IWICBitmap* bmp = nullptr;
    hr = fac->CreateBitmapFromMemory(w, h, GUID_WICPixelFormat32bppBGRA, w * 4, w * 4 * h, const_cast<BYTE*>(data), &bmp);
    if (FAILED(hr) || !bmp) { if (props) props->Release(); frame->Release(); enc->Release(); stream->Release(); return false; }
    hr = frame->WriteSource(bmp, nullptr);
    if (SUCCEEDED(hr)) hr = frame->Commit();
    if (SUCCEEDED(hr)) hr = enc->Commit();
    bmp->Release(); if (props) props->Release(); frame->Release(); enc->Release(); stream->Release();
    return SUCCEEDED(hr);
}

static void crop_bgra(const std::vector<unsigned char>& src, UINT sw, UINT sh, UINT x1, UINT y1, UINT x2, UINT y2, std::vector<unsigned char>& out, UINT& ow, UINT& oh) {
    if (x2 < x1) std::swap(x1, x2); if (y2 < y1) std::swap(y1, y2);
    if (x1 >= sw) x1 = sw ? (sw - 1) : 0; if (y1 >= sh) y1 = sh ? (sh - 1) : 0;
    if (x2 > sw) x2 = sw; if (y2 > sh) y2 = sh;
    ow = (x2 > x1) ? (x2 - x1) : 1; oh = (y2 > y1) ? (y2 - y1) : 1;
    out.resize((size_t)ow * oh * 4);
    const UINT stride = sw * 4; const UINT ostride = ow * 4;
    for (UINT y = 0; y < oh; ++y) {
        const unsigned char* s = src.data() + (size_t)(y1 + y) * stride + (size_t)x1 * 4;
        unsigned char* d = out.data() + (size_t)y * ostride;
        memcpy(d, s, ostride);
    }
}

static void bgra_to_gray(const std::vector<unsigned char>& src, UINT w, UINT h, std::vector<float>& out) {
    out.resize((size_t)w * h);
    for (size_t i = 0, j = 0; i < (size_t)w * h; ++i, j += 4) {
        float b = src[j + 0], g = src[j + 1], r = src[j + 2];
        out[i] = (r * 0.299f + g * 0.587f + b * 0.114f) / 255.0f;
    }
}

// Compute maximum normalized cross-correlation (NCC) between image and template (both grayscale in [0,1])
static double max_ncc(const std::vector<float>& img, UINT iw, UINT ih, const std::vector<float>& tpl, UINT tw, UINT th) {
    if (tw > iw || th > ih) return -1.0;
    // Precompute template mean and variance
    double sumT = 0.0, sumT2 = 0.0;
    for (size_t y = 0; y < th; ++y) {
        for (size_t x = 0; x < tw; ++x) {
            double v = tpl[y * tw + x];
            sumT += v; sumT2 += v * v;
        }
    }
    double meanT = sumT / (double)(tw * th);
    double varT = sumT2 / (double)(tw * th) - meanT * meanT;
    if (varT <= 1e-8) varT = 1e-8;

    double best = -1.0;
    for (UINT y = 0; y + th <= ih; ++y) {
        for (UINT x = 0; x + tw <= iw; ++x) {
            double sumI = 0.0, sumI2 = 0.0, sumIT = 0.0;
            for (UINT j = 0; j < th; ++j) {
                const float* ip = img.data() + (size_t)(y + j) * iw + x;
                const float* tp = tpl.data() + (size_t)j * tw;
                for (UINT i2 = 0; i2 < tw; ++i2) {
                    double vi = ip[i2];
                    double vt = tp[i2];
                    sumI += vi; sumI2 += vi * vi; sumIT += vi * vt;
                }
            }
            double meanI = sumI / (double)(tw * th);
            double varI = sumI2 / (double)(tw * th) - meanI * meanI;
            if (varI <= 1e-8) varI = 1e-8;
            // Covariance
            double cov = sumIT / (double)(tw * th) - meanI * meanT;
            double ncc = cov / (std::sqrt(varI) * std::sqrt(varT));
            if (ncc > best) best = ncc;
        }
    }
    return best;
}

// Simple helper to append log via callback
typedef int (*cb_take_screenshot_t)(void* ctx, const wchar_t* source_name, const wchar_t* out_path);
typedef int (*cb_start_recording_t)(void* ctx);
typedef int (*cb_stop_recording_t)(void* ctx);
typedef int (*cb_is_recording_t)(void* ctx, int* out_state);
typedef void (*cb_event_t)(void* ctx, int ev, double ts);
typedef void (*cb_log_t)(void* ctx, const wchar_t* msg);

static void log_msg(cb_log_t log, void* ctx, const wchar_t* msg) {
    if (log) log(ctx, msg);
}

// ---------------- Double Battle monitor -----------------
struct DoubleState {
    std::atomic<bool> stop{false};
    std::thread th;
};

extern "C" __declspec(dllexport) void* start_double_battle_w(
    const wchar_t* base_dir,
    const wchar_t* source_name,
    const wchar_t* haisinyou_path,
    const wchar_t* koutiku_dir,
    const wchar_t* out_ext,
    double interval_sec,
    cb_take_screenshot_t cb_shot,
    cb_log_t cb_log,
    void* ctx
) {
    if (!base_dir || !source_name || !cb_shot) return nullptr;
    auto st = new DoubleState();
    try {
        st->th = std::thread([=]() {
            ComInit co; WicFactory wf; if (!wf.ok()) return;
            std::wstring handan = std::wstring(base_dir) + L"\\handantmp";
            std::wstring haisin = std::wstring(base_dir) + L"\\haisin";
            std::wstring scene_path = handan + L"\\scene.png";
            std::wstring cropped_path = handan + L"\\screenshot_cropped.png";
            std::wstring masu_path = handan + L"\\masu.png";
            std::wstring masu_area_path = handan + L"\\masu_area.png";
            std::wstring haisinsens_path = haisin + L"\\haisinsensyutu.png";

            // Rects
            UINT masu_x1 = 1541, masu_y1 = 229, masu_x2 = 1651, masu_y2 = 843;
            UINT ss_x1 = 1221, ss_y1 = 150, ss_x2 = 1655, ss_y2 = 850;

            auto sleep_until_stop = [&](double sec)->bool { // true if stopped
                if (sec <= 0) return st->stop.load();
                const int ms = (int)(sec * 1000.0);
                for (int i = 0; i < ms; i += 50) {
                    if (st->stop.load()) return true; std::this_thread::sleep_for(std::chrono::milliseconds(50));
                }
                return st->stop.load();
            };

            if (cb_log) cb_log(ctx, L"[ダブルバトル/N] スレッド開始");

            while (!st->stop.load()) {
                // screenshot
                cb_shot(ctx, source_name, scene_path.c_str());
                // load scene
                std::vector<unsigned char> scene; UINT sw=0, sh=0;
                if (!load_image_bgra(wf.fac, scene_path.c_str(), scene, sw, sh)) {
                    if (sleep_until_stop(0.2)) break; else continue;
                }
                // crop screenshot rect and save
                std::vector<unsigned char> shot; UINT cw=0, ch=0;
                crop_bgra(scene, sw, sh, ss_x1, ss_y1, ss_x2, ss_y2, shot, cw, ch);
                save_image_bgra(wf.fac, cropped_path.c_str(), shot.data(), cw, ch);
                if (cb_log) cb_log(ctx, L"[ダブルバトル/N] screenshot_cropped.png を出力");

                // masu template
                std::vector<unsigned char> masu_img; UINT mw=0, mh=0;
                if (!load_image_bgra(wf.fac, masu_path.c_str(), masu_img, mw, mh)) {
                    if (cb_log) cb_log(ctx, L"[ダブルバトル/N] masu.png を読み込めません");
                    if (sleep_until_stop(interval_sec)) break; else continue;
                }
                // crop area
                std::vector<unsigned char> masu_area; UINT aw=0, ah=0;
                crop_bgra(scene, sw, sh, masu_x1, masu_y1, masu_x2, masu_y2, masu_area, aw, ah);
                save_image_bgra(wf.fac, masu_area_path.c_str(), masu_area.data(), aw, ah);

                // NCC match (grayscale)
                std::vector<float> area_g, masu_g; bgra_to_gray(masu_area, aw, ah, area_g); bgra_to_gray(masu_img, mw, mh, masu_g);
                double score = max_ncc(area_g, aw, ah, masu_g, mw, mh);
                if (score >= 0.6) {
                    if (cb_log) cb_log(ctx, L"[ダブルバトル/N] 'masu' テンプレートを検出");
                    // Write broadcast
                    if (haisinyou_path && *haisinyou_path) {
                        save_image_bgra(wf.fac, haisinyou_path, shot.data(), cw, ch);
                    }
                    // Save koutiku with timestamp
                    if (koutiku_dir && *koutiku_dir) {
                        SYSTEMTIME stime; GetLocalTime(&stime);
                        wchar_t name[128];
                        const wchar_t* ext = (out_ext && *out_ext) ? out_ext : L"png";
                        swprintf(name, 128, L"%04d-%02d-%02d_%02d-%02d-%02d.%s",
                                 stime.wYear, stime.wMonth, stime.wDay, stime.wHour, stime.wMinute, stime.wSecond, ext);
                        std::wstring out = std::wstring(koutiku_dir) + L"\\" + name;
                        save_image_bgra(wf.fac, out.c_str(), shot.data(), cw, ch);
                        if (cb_log) cb_log(ctx, L"[ダブルバトル/N] 構築画像を保存");
                    }

                    // While masu keeps matching, try to detect tag rows and write combined
                    while (!st->stop.load()) {
                        // refresh
                        cb_shot(ctx, source_name, scene_path.c_str());
                        if (!load_image_bgra(wf.fac, scene_path.c_str(), scene, sw, sh)) break;
                        crop_bgra(scene, sw, sh, masu_x1, masu_y1, masu_x2, masu_y2, masu_area, aw, ah);
                        save_image_bgra(wf.fac, masu_area_path.c_str(), masu_area.data(), aw, ah);
                        bgra_to_gray(masu_area, aw, ah, area_g);
                        score = max_ncc(area_g, aw, ah, masu_g, mw, mh);
                        if (score < 0.6) break;

                        // Prepare 6 row crops
                        struct C { UINT x1,y1,x2,y2; } coords[6] = {
                            {146,138,933,255}, {146,255,933,372}, {146,372,933,489},
                            {146,489,933,606}, {146,606,933,723}, {146,723,933,840}
                        };
                        std::vector<std::vector<unsigned char>> rows(6);
                        std::vector<UINT> rw(6), rh(6);
                        for (int i = 0; i < 6; ++i) crop_bgra(scene, sw, sh, coords[i].x1, coords[i].y1, coords[i].x2, coords[i].y2, rows[i], rw[i], rh[i]);

                        // Load ref tags 1..4
                        std::wstring ref1 = handan + L"\\banme1.jpg";
                        std::wstring ref2 = handan + L"\\banme2.jpg";
                        std::wstring ref3 = handan + L"\\banme3.jpg";
                        std::wstring ref4 = handan + L"\\banme4.jpg";
                        std::vector<unsigned char> t1,t2,t3,t4; UINT t1w=0,t1h=0,t2w=0,t2h=0,t3w=0,t3h=0,t4w=0,t4h=0;
                        if (!load_image_bgra(wf.fac, ref1.c_str(), t1, t1w, t1h) || !load_image_bgra(wf.fac, ref2.c_str(), t2, t2w, t2h) ||
                            !load_image_bgra(wf.fac, ref3.c_str(), t3, t3w, t3h) || !load_image_bgra(wf.fac, ref4.c_str(), t4, t4w, t4h)) {
                            if (sleep_until_stop(1.0)) return; else continue;
                        }
                        std::vector<float> gt1,gt2,gt3,gt4; bgra_to_gray(t1,t1w,t1h,gt1); bgra_to_gray(t2,t2w,t2h,gt2); bgra_to_gray(t3,t3w,t3h,gt3); bgra_to_gray(t4,t4w,t4h,gt4);

                        std::vector<int> matched_idx; matched_idx.reserve(4);
                        auto try_match = [&](const std::vector<unsigned char>& row, UINT rwx, UINT rwy, const std::vector<float>& tpl, UINT twx, UINT twy)->double{
                            std::vector<float> gr; bgra_to_gray(row, rwx, rwy, gr);
                            return max_ncc(gr, rwx, rwy, tpl, twx, twy);
                        };

                        // Greedy matching per tag
                        const double th = 0.8;
                        int used[6] = {0,0,0,0,0,0};
                        double s; int mi;
                        // tag1
                        s = -2.0; mi = -1; for (int i = 0; i < 6; ++i) { if (used[i]) continue; double sc = try_match(rows[i], rw[i], rh[i], gt1, t1w, t1h); if (sc > s) { s = sc; mi = i; } }
                        if (s >= th) { used[mi] = 1; matched_idx.push_back(mi); }
                        else { if (sleep_until_stop(1.0)) return; else continue; }
                        // tag2
                        s = -2.0; mi = -1; for (int i = 0; i < 6; ++i) { if (used[i]) continue; double sc = try_match(rows[i], rw[i], rh[i], gt2, t2w, t2h); if (sc > s) { s = sc; mi = i; } }
                        if (s >= th) { used[mi] = 1; matched_idx.push_back(mi); } else { if (sleep_until_stop(1.0)) return; else continue; }
                        // tag3
                        s = -2.0; mi = -1; for (int i = 0; i < 6; ++i) { if (used[i]) continue; double sc = try_match(rows[i], rw[i], rh[i], gt3, t3w, t3h); if (sc > s) { s = sc; mi = i; } }
                        if (s >= th) { used[mi] = 1; matched_idx.push_back(mi); } else { if (sleep_until_stop(1.0)) return; else continue; }
                        // tag4
                        s = -2.0; mi = -1; for (int i = 0; i < 6; ++i) { if (used[i]) continue; double sc = try_match(rows[i], rw[i], rh[i], gt4, t4w, t4h); if (sc > s) { s = sc; mi = i; } }
                        if (s >= th) { used[mi] = 1; matched_idx.push_back(mi); } else { if (sleep_until_stop(1.0)) return; else continue; }

                        if (matched_idx.size() == 4) {
                            // vconcat rows in matched order
                            UINT outw = rw[matched_idx[0]]; UINT outh = 0; for (int idx : matched_idx) outh += rh[idx];
                            std::vector<unsigned char> outimg; outimg.resize((size_t)outw * outh * 4);
                            UINT yoff = 0; for (int idx : matched_idx) {
                                UINT wrow = rw[idx], hrow = rh[idx];
                                for (UINT y = 0; y < hrow; ++y) {
                                    memcpy(outimg.data() + ((size_t)(yoff + y) * outw * 4), rows[idx].data() + (size_t)y * wrow * 4, (size_t)wrow * 4);
                                }
                                yoff += hrow;
                            }
                            save_image_bgra(wf.fac, haisinsens_path.c_str(), outimg.data(), outw, outh);
                            if (cb_log) cb_log(ctx, L"[ダブルバトル/N] 抽出画像を書き出し");
                        }

                        if (sleep_until_stop(1.0)) return; // periodic poll
                    }
                }

                if (sleep_until_stop(interval_sec)) break;
            }

            if (cb_log) cb_log(ctx, L"[ダブルバトル/N] スレッド停止");
        });
        st->th.detach();
        return st;
    } catch (...) {
        delete st; return nullptr;
    }
}

extern "C" __declspec(dllexport) void stop_double_battle(void* handle) {
    if (!handle) return;
    DoubleState* st = reinterpret_cast<DoubleState*>(handle);
    st->stop.store(true);
    // Detached thread; give it a short chance to exit
    for (int i = 0; i < 40; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    delete st;
}

// ---------------- Rkaisi/Teisi monitor -----------------
struct RecState {
    std::atomic<bool> stop{false};
    std::atomic<bool> recording{false};
    double rec_start_ts{0.0};
    std::thread th;
};

extern "C" __declspec(dllexport) void* start_rkaisi_teisi_w(
    const wchar_t* handan_dir,
    const wchar_t* source_name,
    double match_threshold,
    cb_take_screenshot_t cb_shot,
    cb_start_recording_t cb_start,
    cb_stop_recording_t cb_stop,
    cb_is_recording_t cb_isrec,
    cb_event_t cb_event,
    cb_log_t cb_log,
    void* ctx
) {
    if (!handan_dir || !source_name || !cb_shot) return nullptr;
    auto st = new RecState();
    try {
        st->th = std::thread([=]() {
            ComInit co; WicFactory wf; if (!wf.ok()) return;
            std::wstring scene_path = std::wstring(handan_dir) + L"\\scene2.png";
            std::wstring masu_tpl = std::wstring(handan_dir) + L"\\masu1.png";
            std::wstring mark_tpl = std::wstring(handan_dir) + L"\\mark.png";
            std::wstring masu_crop_path = std::wstring(handan_dir) + L"\\masu1cropped.png";
            std::wstring mark_crop_path = std::wstring(handan_dir) + L"\\markcropped.png";

            UINT masu_x1 = 1541, masu_y1 = 229, masu_x2 = 1651, masu_y2 = 843;
            UINT mark_x1 = 0, mark_y1 = 0, mark_x2 = 96, mark_y2 = 72;

            if (cb_log) cb_log(ctx, L"[録開始/停止/N] スレッド開始");

            while (!st->stop.load()) {
                cb_shot(ctx, source_name, scene_path.c_str());
                std::vector<unsigned char> scene; UINT sw=0, sh=0;
                if (!load_image_bgra(wf.fac, scene_path.c_str(), scene, sw, sh)) { std::this_thread::sleep_for(std::chrono::milliseconds(100)); continue; }

                std::vector<unsigned char> masu_crop, mark_crop; UINT mw=0,mh=0, kw=0,kh=0;
                crop_bgra(scene, sw, sh, masu_x1, masu_y1, masu_x2, masu_y2, masu_crop, mw, mh);
                crop_bgra(scene, sw, sh, mark_x1, mark_y1, mark_x2, mark_y2, mark_crop, kw, kh);
                save_image_bgra(wf.fac, masu_crop_path.c_str(), masu_crop.data(), mw, mh);
                save_image_bgra(wf.fac, mark_crop_path.c_str(), mark_crop.data(), kw, kh);

                std::vector<unsigned char> masu_ref, mark_ref; UINT rw1=0,rh1=0,rw2=0,rh2=0;
                if (!load_image_bgra(wf.fac, masu_tpl.c_str(), masu_ref, rw1, rh1) || !load_image_bgra(wf.fac, mark_tpl.c_str(), mark_ref, rw2, rh2)) {
                    if (cb_log) cb_log(ctx, L"[録開始/停止/N] テンプレートが見つからないため待機");
                    std::this_thread::sleep_for(std::chrono::milliseconds(1000));
                    continue;
                }
                std::vector<float> g_masu_crop, g_masu_ref, g_mark_crop, g_mark_ref;
                bgra_to_gray(masu_crop, mw, mh, g_masu_crop); bgra_to_gray(masu_ref, rw1, rh1, g_masu_ref);
                bgra_to_gray(mark_crop, kw, kh, g_mark_crop); bgra_to_gray(mark_ref, rw2, rh2, g_mark_ref);

                double s_masu = max_ncc(g_masu_crop, mw, mh, g_masu_ref, rw1, rh1);
                double s_mark = max_ncc(g_mark_crop, kw, kh, g_mark_ref, rw2, rh2);

                if (!st->recording.load() && s_masu >= match_threshold) {
                    if (cb_log) cb_log(ctx, L"[録開始/停止/N] 'masu1' 検出 → 録画開始");
                    bool started = false;
                    if (cb_start) { cb_start(ctx); }
                    for (int i = 0; i < 10; ++i) {
                        if (st->stop.load()) break;
                        int rec = -1; if (cb_isrec) cb_isrec(ctx, &rec);
                        if (rec == 1) { started = true; break; }
                        std::this_thread::sleep_for(std::chrono::milliseconds(200));
                    }
                    if (!started && cb_start) {
                        cb_start(ctx);
                        for (int i = 0; i < 10; ++i) {
                            if (st->stop.load()) break;
                            int rec = -1; if (cb_isrec) cb_isrec(ctx, &rec);
                            if (rec == 1) { started = true; break; }
                            std::this_thread::sleep_for(std::chrono::milliseconds(200));
                        }
                    }
                    if (started) {
                        st->recording.store(true);
                        double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
                        st->rec_start_ts = now;
                        if (cb_event) cb_event(ctx, 1, now); // 1 = started
                        // guard period similar to Python (140s)
                        for (int i = 0; i < 1400; ++i) { if (st->stop.load()) break; std::this_thread::sleep_for(std::chrono::milliseconds(100)); }
                    } else {
                        if (cb_log) cb_log(ctx, L"[録開始/停止/N] 録画が開始されませんでした");
                        std::this_thread::sleep_for(std::chrono::milliseconds(1000));
                    }
                }

                if (st->recording.load() && s_mark >= match_threshold) {
                    if (cb_log) cb_log(ctx, L"[録開始/停止/N] 'mark' 検出 → 録画停止");
                    if (cb_event) cb_event(ctx, 2, std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()); // 2 = stop marker
                    bool stopped = false;
                    if (cb_stop) cb_stop(ctx);
                    for (int i = 0; i < 10; ++i) {
                        if (st->stop.load()) break;
                        int rec = -1; if (cb_isrec) cb_isrec(ctx, &rec);
                        if (rec == 0) { stopped = true; break; }
                        std::this_thread::sleep_for(std::chrono::milliseconds(200));
                    }
                    if (!stopped && cb_stop) {
                        cb_stop(ctx);
                        for (int i = 0; i < 10; ++i) {
                            if (st->stop.load()) break;
                            int rec = -1; if (cb_isrec) cb_isrec(ctx, &rec);
                            if (rec == 0) { stopped = true; break; }
                            std::this_thread::sleep_for(std::chrono::milliseconds(200));
                        }
                    }
                    st->recording.store(stopped ? false : st->recording.load());
                }
            }

            // Cleanup on exit: stop recording if still active
            if (st->recording.load()) {
                if (cb_log) cb_log(ctx, L"[録開始/停止/N] 終了時に録画を停止します");
                if (cb_stop) cb_stop(ctx);
                if (cb_event) cb_event(ctx, 3, std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()); // 3 = stopped on exit
                st->recording.store(false);
            }

            if (cb_log) cb_log(ctx, L"[録開始/停止/N] スレッド停止");
        });
        st->th.detach();
        return st;
    } catch (...) {
        delete st; return nullptr;
    }
}

extern "C" __declspec(dllexport) void stop_rkaisi_teisi(void* handle) {
    if (!handle) return;
    RecState* st = reinterpret_cast<RecState*>(handle);
    st->stop.store(true);
    for (int i = 0; i < 40; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    delete st;
}
