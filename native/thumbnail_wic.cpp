// Minimal WIC-based thumbnail generator for Windows
// Build (MinGW):
//   g++ -std=c++17 -O2 -shared -o build/thumbnail_wic.dll thumbnail_wic.cpp -lole32 -lwindowscodecs

#include <windows.h>
#include <wincodec.h>
#include <objbase.h>
#include <cwchar>
#include <vector>
#include <cmath>

// Internal helpers for loading images and doing simple grayscale template matching
namespace {
    struct ComInit {
        bool inited{false};
        ComInit() {
            HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
            inited = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;
        }
        ~ComInit() {
            if (inited) CoUninitialize();
        }
    };

    HRESULT create_factory(IWICImagingFactory** out) {
        if (!out) return E_POINTER;
        *out = nullptr;
        return CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(out));
    }

    // Load image and convert to 32bpp BGRA, returning raw bytes
    HRESULT load_bgra(IWICImagingFactory* factory, const wchar_t* path, UINT& w, UINT& h, std::vector<BYTE>& bgra) {
        if (!factory || !path) return E_POINTER;
        w = h = 0;
        bgra.clear();
        IWICBitmapDecoder* decoder = nullptr;
        HRESULT hr = factory->CreateDecoderFromFilename(path, nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
        if (FAILED(hr) || !decoder) return E_FAIL;
        IWICBitmapFrameDecode* frame = nullptr;
        hr = decoder->GetFrame(0, &frame);
        if (FAILED(hr) || !frame) {
            if (decoder) decoder->Release();
            return E_FAIL;
        }
        WICPixelFormatGUID dstFmt = GUID_WICPixelFormat32bppBGRA;
        IWICFormatConverter* conv = nullptr;
        hr = factory->CreateFormatConverter(&conv);
        if (SUCCEEDED(hr) && conv) {
            hr = conv->Initialize(frame, dstFmt, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
        }
        if (FAILED(hr) || !conv) {
            if (conv) conv->Release();
            frame->Release();
            decoder->Release();
            return E_FAIL;
        }
        hr = conv->GetSize(&w, &h);
        if (FAILED(hr) || w == 0 || h == 0) {
            conv->Release();
            frame->Release();
            decoder->Release();
            return E_FAIL;
        }
        const UINT stride = w * 4u;
        bgra.resize(static_cast<size_t>(stride) * static_cast<size_t>(h));
        hr = conv->CopyPixels(nullptr, stride, static_cast<UINT>(bgra.size()), bgra.data());
        conv->Release();
        frame->Release();
        decoder->Release();
        return hr;
    }

    // Convert BGRA buffer to single-channel grayscale (float)
    void to_grayscale(const std::vector<BYTE>& bgra, UINT w, UINT h, std::vector<float>& gray) {
        gray.resize(static_cast<size_t>(w) * static_cast<size_t>(h));
        const size_t stride = static_cast<size_t>(w) * 4u;
        for (UINT y = 0; y < h; ++y) {
            const BYTE* row = bgra.data() + y * stride;
            float* gout = gray.data() + static_cast<size_t>(y) * static_cast<size_t>(w);
            for (UINT x = 0; x < w; ++x) {
                BYTE B = row[x * 4 + 0];
                BYTE G = row[x * 4 + 1];
                BYTE R = row[x * 4 + 2];
                // Luma approximation
                gout[x] = 0.114f * float(B) + 0.587f * float(G) + 0.299f * float(R);
            }
        }
    }

    // Compute TM_CCOEFF_NORMED and check if any location >= threshold
    // Returns 1 if matched anywhere, 0 otherwise
    int match_templ_gray(const std::vector<float>& img, UINT iw, UINT ih,
                         const std::vector<float>& tpl, UINT tw, UINT th, float threshold) {
        if (iw < tw || ih < th || tw == 0 || th == 0) return 0;
        const size_t N = static_cast<size_t>(tw) * static_cast<size_t>(th);
        double sumT = 0.0, sumT2 = 0.0;
        for (UINT ty = 0; ty < th; ++ty) {
            const float* trow = tpl.data() + static_cast<size_t>(ty) * static_cast<size_t>(tw);
            for (UINT tx = 0; tx < tw; ++tx) {
                const double v = trow[tx];
                sumT += v;
                sumT2 += v * v;
            }
        }
        const double denomT = sumT2 - (sumT * sumT) / double(N);
        const double eps = 1e-12;

        for (UINT y = 0; y + th <= ih; ++y) {
            for (UINT x = 0; x + tw <= iw; ++x) {
                double S = 0.0, S2 = 0.0, ST = 0.0;
                for (UINT ty = 0; ty < th; ++ty) {
                    const float* irow = img.data() + (static_cast<size_t>(y + ty) * static_cast<size_t>(iw) + x);
                    const float* trow = tpl.data() + static_cast<size_t>(ty) * static_cast<size_t>(tw);
                    for (UINT tx = 0; tx < tw; ++tx) {
                        const double vi = irow[tx];
                        const double vt = trow[tx];
                        S += vi;
                        S2 += vi * vi;
                        ST += vi * vt;
                    }
                }
                const double denomI = S2 - (S * S) / double(N);
                const double denom = std::sqrt(std::max(denomT, eps) * std::max(denomI, eps));
                double r = 0.0;
                if (denom > eps) {
                    const double num = ST - (sumT * S) / double(N);
                    r = num / denom;
                }
                if (r >= double(threshold)) {
                    return 1; // found
                }
            }
        }
        return 0;
    }
}

extern "C" __declspec(dllexport) int gen_thumbnail_w(const wchar_t* in_path, const wchar_t* out_path, int max_w)
{
    if (!in_path || !out_path || max_w <= 0) {
        return 2; // invalid args
    }

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    bool co_inited = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;

    IWICImagingFactory* factory = nullptr;
    hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory));
    if (FAILED(hr) || !factory) {
        if (co_inited) CoUninitialize();
        return 3; // factory failure
    }

    IWICBitmapDecoder* decoder = nullptr;
    hr = factory->CreateDecoderFromFilename(in_path, nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
    if (FAILED(hr) || !decoder) {
        factory->Release();
        if (co_inited) CoUninitialize();
        return 4; // decode open failure
    }

    IWICBitmapFrameDecode* frame = nullptr;
    hr = decoder->GetFrame(0, &frame);
    if (FAILED(hr) || !frame) {
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 5; // frame failure
    }

    UINT w = 0, h = 0;
    frame->GetSize(&w, &h);
    if (w == 0 || h == 0) {
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 6; // invalid size
    }

    double scale = (w > (UINT)max_w) ? (double)max_w / (double)w : 1.0;
    UINT tw = (UINT)(w * scale);
    UINT th = (UINT)(h * scale);
    if (tw == 0) tw = 1;
    if (th == 0) th = 1;

    IWICBitmapSource* src = frame; // default: original
    IWICBitmapScaler* scaler = nullptr;
    if (scale < 1.0) {
        hr = factory->CreateBitmapScaler(&scaler);
        if (SUCCEEDED(hr) && scaler) {
            // Fant is good quality downscale; Box is faster but lower quality
            hr = scaler->Initialize(frame, tw, th, WICBitmapInterpolationModeFant);
            if (SUCCEEDED(hr)) {
                src = scaler;
            }
        }
    }

    // Choose container by extension (default: PNG)
    const wchar_t* dot = wcsrchr(out_path, L'.');
    GUID container = GUID_ContainerFormatPng;
    if (dot) {
        if (_wcsicmp(dot, L".jpg") == 0 || _wcsicmp(dot, L".jpeg") == 0) {
            container = GUID_ContainerFormatJpeg;
        } else if (_wcsicmp(dot, L".png") == 0) {
            container = GUID_ContainerFormatPng;
        }
    }

    IWICStream* stream = nullptr;
    hr = factory->CreateStream(&stream);
    if (SUCCEEDED(hr) && stream) {
        hr = stream->InitializeFromFilename(out_path, GENERIC_WRITE);
    }
    if (FAILED(hr) || !stream) {
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 7; // stream failure
    }

    IWICBitmapEncoder* encoder = nullptr;
    hr = factory->CreateEncoder(container, nullptr, &encoder);
    if (FAILED(hr) || !encoder) {
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 8; // encoder create failure
    }

    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        encoder->Release();
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 9; // encoder init failure
    }

    IWICBitmapFrameEncode* outFrame = nullptr;
    IPropertyBag2* props = nullptr;
    hr = encoder->CreateNewFrame(&outFrame, &props);
    if (FAILED(hr) || !outFrame) {
        if (props) props->Release();
        encoder->Release();
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 10; // frame encode create failure
    }

    if (container == GUID_ContainerFormatJpeg && props) {
        PROPBAG2 opt = {};
        opt.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
        VARIANT var; VariantInit(&var);
        var.vt = VT_R4; var.fltVal = 0.85f; // 85% quality
        props->Write(1, &opt, &var);
        VariantClear(&var);
    }

    hr = outFrame->Initialize(props);
    if (FAILED(hr)) {
        if (props) props->Release();
        outFrame->Release();
        encoder->Release();
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 11; // frame init failure
    }

    hr = outFrame->SetSize(tw, th);
    if (FAILED(hr)) {
        if (props) props->Release();
        outFrame->Release();
        encoder->Release();
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 12; // size set failure
    }

    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
    hr = outFrame->SetPixelFormat(&fmt);
    if (FAILED(hr)) {
        if (props) props->Release();
        outFrame->Release();
        encoder->Release();
        stream->Release();
        if (scaler) scaler->Release();
        frame->Release();
        decoder->Release();
        factory->Release();
        if (co_inited) CoUninitialize();
        return 13; // pixel format failure
    }

    // Convert source to required format if needed
    IWICFormatConverter* conv = nullptr;
    IWICBitmapSource* encSrc = src;
    WICPixelFormatGUID srcFmt;
    src->GetPixelFormat(&srcFmt);
    if (memcmp(&srcFmt, &fmt, sizeof(GUID)) != 0) {
        hr = factory->CreateFormatConverter(&conv);
        if (SUCCEEDED(hr) && conv) {
            hr = conv->Initialize(src, fmt, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
            if (SUCCEEDED(hr)) {
                encSrc = conv;
            }
        }
    }

    if (SUCCEEDED(hr)) hr = outFrame->WriteSource(encSrc, nullptr);
    if (SUCCEEDED(hr)) hr = outFrame->Commit();
    if (SUCCEEDED(hr)) hr = encoder->Commit();

    if (conv) conv->Release();
    if (props) props->Release();
    outFrame->Release();
    encoder->Release();
    stream->Release();
    if (scaler) scaler->Release();
    frame->Release();
    decoder->Release();
    factory->Release();
    if (co_inited) CoUninitialize();

    return SUCCEEDED(hr) ? 0 : 14;
}

// Batch thumbnails: arrays of input/output paths
extern "C" __declspec(dllexport) int gen_thumbnails_w(const wchar_t** in_paths,
                                                      int count,
                                                      const wchar_t** out_paths,
                                                      int max_w)
{
    if (!in_paths || !out_paths || count <= 0 || max_w <= 0) return 2;
    int ok = 0;
    for (int i = 0; i < count; ++i) {
        const wchar_t* in_p = in_paths[i];
        const wchar_t* out_p = out_paths[i];
        if (!in_p || !out_p) continue;
        int rc = gen_thumbnail_w(in_p, out_p, max_w);
        if (rc == 0) ok++;
    }
    return ok; // number of successes
}

// Crop and resize to a max width (keep aspect). If scale >= 1, no upscale.
extern "C" __declspec(dllexport) int crop_resize_w(const wchar_t* in_path,
                                                   const wchar_t* out_path,
                                                   int x, int y, int w, int h,
                                                   int max_w)
{
    if (!in_path || !out_path || w <= 0 || h <= 0 || max_w <= 0) return 2;
    ComInit com;
    IWICImagingFactory* factory = nullptr;
    HRESULT hr = create_factory(&factory);
    if (FAILED(hr) || !factory) return 3;

    // Decode source
    IWICBitmapDecoder* decoder = nullptr;
    hr = factory->CreateDecoderFromFilename(in_path, nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
    if (FAILED(hr) || !decoder) { factory->Release(); return 4; }
    IWICBitmapFrameDecode* frame = nullptr;
    hr = decoder->GetFrame(0, &frame);
    if (FAILED(hr) || !frame) { decoder->Release(); factory->Release(); return 5; }

    // Clip region
    IWICBitmapClipper* clip = nullptr;
    hr = factory->CreateBitmapClipper(&clip);
    if (FAILED(hr) || !clip) { frame->Release(); decoder->Release(); factory->Release(); return 6; }
    WICRect rc; rc.X = x; rc.Y = y; rc.Width = w; rc.Height = h;
    hr = clip->Initialize(frame, &rc);
    if (FAILED(hr)) { clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 7; }

    // Convert to 32bpp BGRA for reliable scaler path
    IWICFormatConverter* conv = nullptr;
    hr = factory->CreateFormatConverter(&conv);
    if (FAILED(hr) || !conv) { clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 8; }
    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
    hr = conv->Initialize(clip, fmt, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) { conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 9; }

    UINT cw = 0, ch = 0;
    conv->GetSize(&cw, &ch);
    if (cw == 0 || ch == 0) { conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 10; }

    double scale = (cw > (UINT)max_w) ? (double)max_w / (double)cw : 1.0;
    UINT tw = (UINT)(cw * scale);
    UINT th = (UINT)(ch * scale);
    if (tw == 0) tw = 1; if (th == 0) th = 1;

    IWICBitmapSource* srcScaled = conv;
    IWICBitmapScaler* scaler = nullptr;
    if (scale < 1.0) {
        hr = factory->CreateBitmapScaler(&scaler);
        if (SUCCEEDED(hr) && scaler) {
            hr = scaler->Initialize(conv, tw, th, WICBitmapInterpolationModeFant);
            if (SUCCEEDED(hr)) srcScaled = scaler; else { scaler->Release(); scaler = nullptr; }
        }
    }

    // Choose container by extension
    const wchar_t* dot = wcsrchr(out_path, L'.');
    GUID container = GUID_ContainerFormatPng;
    if (dot) {
        if (_wcsicmp(dot, L".jpg") == 0 || _wcsicmp(dot, L".jpeg") == 0) container = GUID_ContainerFormatJpeg;
        else if (_wcsicmp(dot, L".png") == 0) container = GUID_ContainerFormatPng;
    }

    IWICStream* stream = nullptr;
    hr = factory->CreateStream(&stream);
    if (SUCCEEDED(hr) && stream) hr = stream->InitializeFromFilename(out_path, GENERIC_WRITE);
    if (FAILED(hr) || !stream) { if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 11; }

    IWICBitmapEncoder* encoder = nullptr;
    hr = factory->CreateEncoder(container, nullptr, &encoder);
    if (FAILED(hr) || !encoder) { stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 12; }
    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) { encoder->Release(); stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 13; }

    IWICBitmapFrameEncode* outFrame = nullptr; IPropertyBag2* props = nullptr;
    hr = encoder->CreateNewFrame(&outFrame, &props);
    if (FAILED(hr) || !outFrame) { if (props) props->Release(); encoder->Release(); stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 14; }
    hr = outFrame->Initialize(props);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 15; }
    hr = outFrame->SetSize(tw, th);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 16; }
    WICPixelFormatGUID pf = GUID_WICPixelFormat32bppBGRA;
    hr = outFrame->SetPixelFormat(&pf);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); if (scaler) scaler->Release(); conv->Release(); clip->Release(); frame->Release(); decoder->Release(); factory->Release(); return 17; }
    IWICBitmapSource* src = srcScaled;
    hr = outFrame->WriteSource(src, nullptr);
    if (SUCCEEDED(hr)) hr = outFrame->Commit();
    if (SUCCEEDED(hr)) hr = encoder->Commit();

    if (props) props->Release();
    outFrame->Release();
    encoder->Release();
    stream->Release();
    if (scaler) scaler->Release();
    conv->Release();
    clip->Release();
    frame->Release();
    decoder->Release();
    factory->Release();
    return SUCCEEDED(hr) ? 0 : 18;
}

// Vertical concatenate images (match widths to the minimum width)
extern "C" __declspec(dllexport) int vconcat_w(const wchar_t** in_paths,
                                               int count,
                                               const wchar_t* out_path)
{
    if (!in_paths || !out_path || count <= 0) return 2;
    ComInit com;
    IWICImagingFactory* factory = nullptr;
    HRESULT hr = create_factory(&factory);
    if (FAILED(hr) || !factory) return 3;

    struct Img { UINT w; UINT h; std::vector<BYTE> bgra; };
    std::vector<Img> items;
    items.reserve(static_cast<size_t>(count));

    // First pass: load and convert to BGRA, track min width
    UINT minw = 0xFFFFFFFFu;
    for (int i = 0; i < count; ++i) {
        if (!in_paths[i]) { factory->Release(); return 4; }
        UINT w = 0, h = 0; std::vector<BYTE> buf;
        if (FAILED(load_bgra(factory, in_paths[i], w, h, buf))) { factory->Release(); return 5; }
        if (w == 0 || h == 0) { factory->Release(); return 6; }
        if (w < minw) minw = w;
        items.push_back(Img{w, h, std::move(buf)});
    }
    if (minw == 0xFFFFFFFFu) { factory->Release(); return 7; }

    // Second: scale each to minw
    std::vector<std::vector<BYTE>> scaled;
    std::vector<UINT> sh;
    scaled.resize(items.size());
    sh.resize(items.size());

    for (size_t i = 0; i < items.size(); ++i) {
        auto& it = items[i];
        UINT tw = minw;
        double scale = (it.w > tw) ? double(tw) / double(it.w) : (it.w == tw ? 1.0 : double(tw) / double(it.w));
        UINT th = (UINT)std::max(1.0, std::round(double(it.h) * scale));

        if (scale == 1.0) {
            // No scaling: copy
            scaled[i] = it.bgra; sh[i] = it.h;
        } else {
            // Create WIC bitmap from memory and scale
            IWICBitmap* mem = nullptr;
            const UINT srcStride = it.w * 4u;
            hr = factory->CreateBitmapFromMemory(it.w, it.h, GUID_WICPixelFormat32bppBGRA, srcStride, (UINT)it.bgra.size(), it.bgra.data(), &mem);
            if (FAILED(hr) || !mem) { factory->Release(); return 8; }
            IWICBitmapScaler* scaler = nullptr;
            hr = factory->CreateBitmapScaler(&scaler);
            if (FAILED(hr) || !scaler) { mem->Release(); factory->Release(); return 9; }
            hr = scaler->Initialize(mem, tw, th, WICBitmapInterpolationModeFant);
            if (FAILED(hr)) { scaler->Release(); mem->Release(); factory->Release(); return 10; }
            const UINT stride = tw * 4u;
            scaled[i].resize((size_t)stride * (size_t)th);
            hr = scaler->CopyPixels(nullptr, stride, (UINT)scaled[i].size(), scaled[i].data());
            scaler->Release(); mem->Release();
            if (FAILED(hr)) { factory->Release(); return 11; }
            sh[i] = th;
        }
    }

    // Compose big image
    UINT total_h = 0; for (auto v : sh) total_h += v;
    const UINT stride = minw * 4u;
    std::vector<BYTE> big;
    big.resize((size_t)stride * (size_t)total_h);
    size_t off = 0;
    for (size_t i = 0; i < scaled.size(); ++i) {
        const auto& buf = scaled[i];
        const UINT h = sh[i];
        const size_t bytes = (size_t)stride * (size_t)h;
        memcpy(big.data() + off, buf.data(), bytes);
        off += bytes;
    }

    // Encode to out_path (PNG/JPEG based on ext)
    const wchar_t* dot = wcsrchr(out_path, L'.');
    GUID container = GUID_ContainerFormatPng;
    if (dot) {
        if (_wcsicmp(dot, L".jpg") == 0 || _wcsicmp(dot, L".jpeg") == 0) container = GUID_ContainerFormatJpeg;
        else if (_wcsicmp(dot, L".png") == 0) container = GUID_ContainerFormatPng;
    }
    IWICStream* stream = nullptr; hr = factory->CreateStream(&stream);
    if (SUCCEEDED(hr) && stream) hr = stream->InitializeFromFilename(out_path, GENERIC_WRITE);
    if (FAILED(hr) || !stream) { factory->Release(); return 12; }
    IWICBitmapEncoder* encoder = nullptr; hr = factory->CreateEncoder(container, nullptr, &encoder);
    if (FAILED(hr) || !encoder) { stream->Release(); factory->Release(); return 13; }
    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) { encoder->Release(); stream->Release(); factory->Release(); return 14; }
    IWICBitmapFrameEncode* outFrame = nullptr; IPropertyBag2* props = nullptr; hr = encoder->CreateNewFrame(&outFrame, &props);
    if (FAILED(hr) || !outFrame) { if (props) props->Release(); encoder->Release(); stream->Release(); factory->Release(); return 15; }
    if (container == GUID_ContainerFormatJpeg && props) {
        PROPBAG2 opt = {}; opt.pstrName = const_cast<LPOLESTR>(L"ImageQuality"); VARIANT var; VariantInit(&var); var.vt = VT_R4; var.fltVal = 0.9f; props->Write(1, &opt, &var); VariantClear(&var);
    }
    hr = outFrame->Initialize(props);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); factory->Release(); return 16; }
    hr = outFrame->SetSize(minw, total_h);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); factory->Release(); return 17; }
    WICPixelFormatGUID pf = GUID_WICPixelFormat32bppBGRA; hr = outFrame->SetPixelFormat(&pf);
    if (FAILED(hr)) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); factory->Release(); return 18; }
    // Wrap big buffer as IWICBitmap and write
    IWICBitmap* bmp = nullptr; hr = factory->CreateBitmapFromMemory(minw, total_h, GUID_WICPixelFormat32bppBGRA, stride, (UINT)big.size(), big.data(), &bmp);
    if (FAILED(hr) || !bmp) { if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); factory->Release(); return 19; }
    hr = outFrame->WriteSource(bmp, nullptr);
    if (SUCCEEDED(hr)) hr = outFrame->Commit();
    if (SUCCEEDED(hr)) hr = encoder->Commit();
    bmp->Release();
    if (props) props->Release(); outFrame->Release(); encoder->Release(); stream->Release(); factory->Release();
    return SUCCEEDED(hr) ? 0 : 20;
}
// Export: match_template over full image
// Returns 0 on success, and writes 1 (match) or 0 (no match) to out_match.
// Non-zero return indicates an error.
extern "C" __declspec(dllexport) int match_template_w(const wchar_t* image_path,
                                                      const wchar_t* templ_path,
                                                      float threshold,
                                                      int* out_match)
{
    if (!image_path || !templ_path || !out_match) return 2;
    *out_match = 0;
    ComInit com;
    IWICImagingFactory* factory = nullptr;
    HRESULT hr = create_factory(&factory);
    if (FAILED(hr) || !factory) return 3;
    UINT iw = 0, ih = 0, tw = 0, th = 0;
    std::vector<BYTE> ibgr, tbgr;
    if (FAILED(load_bgra(factory, image_path, iw, ih, ibgr))) { factory->Release(); return 4; }
    if (FAILED(load_bgra(factory, templ_path, tw, th, tbgr))) { factory->Release(); return 5; }
    factory->Release();
    std::vector<float> igray, tgray;
    to_grayscale(ibgr, iw, ih, igray);
    to_grayscale(tbgr, tw, th, tgray);
    int matched = match_templ_gray(igray, iw, ih, tgray, tw, th, threshold);
    *out_match = matched ? 1 : 0;
    return 0;
}

// Export: match_template but restricts search to a given rect (x,y,w,h)
extern "C" __declspec(dllexport) int match_template_region_w(const wchar_t* image_path,
                                                             int x, int y, int w, int h,
                                                             const wchar_t* templ_path,
                                                             float threshold,
                                                             int* out_match)
{
    if (!image_path || !templ_path || !out_match) return 2;
    *out_match = 0;
    if (w <= 0 || h <= 0 || x < 0 || y < 0) return 2;
    ComInit com;
    IWICImagingFactory* factory = nullptr;
    HRESULT hr = create_factory(&factory);
    if (FAILED(hr) || !factory) return 3;
    UINT iw = 0, ih = 0, tw = 0, th = 0;
    std::vector<BYTE> ibgr, tbgr;
    if (FAILED(load_bgra(factory, image_path, iw, ih, ibgr))) { factory->Release(); return 4; }
    if (FAILED(load_bgra(factory, templ_path, tw, th, tbgr))) { factory->Release(); return 5; }
    factory->Release();
    // Bounds check and clamp region to image size
    if (x >= (int)iw || y >= (int)ih) return 0; // no search area
    int rx = x;
    int ry = y;
    int rw = w;
    int rh = h;
    if (rx < 0) rx = 0;
    if (ry < 0) ry = 0;
    if (rx + rw > (int)iw) rw = (int)iw - rx;
    if (ry + rh > (int)ih) rh = (int)ih - ry;
    if (rw <= 0 || rh <= 0) return 0;

    std::vector<float> igray, tgray;
    to_grayscale(ibgr, iw, ih, igray);
    to_grayscale(tbgr, tw, th, tgray);

    // Extract ROI into contiguous buffer to simplify search bounds
    std::vector<float> roi;
    roi.resize(static_cast<size_t>(rw) * static_cast<size_t>(rh));
    for (int yy = 0; yy < rh; ++yy) {
        const float* src = igray.data() + (static_cast<size_t>(ry + yy) * static_cast<size_t>(iw) + rx);
        float* dst = roi.data() + static_cast<size_t>(yy) * static_cast<size_t>(rw);
        for (int xx = 0; xx < rw; ++xx) dst[xx] = src[xx];
    }

    int matched = match_templ_gray(roi, static_cast<UINT>(rw), static_cast<UINT>(rh), tgray, tw, th, threshold);
    *out_match = matched ? 1 : 0;
    return 0;
}

