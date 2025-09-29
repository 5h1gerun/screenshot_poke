// Minimal WIC-based thumbnail generator for Windows
// Build (MinGW):
//   g++ -std=c++17 -O2 -shared -o build/thumbnail_wic.dll thumbnail_wic.cpp -lole32 -lwindowscodecs

#include <windows.h>
#include <wincodec.h>
#include <objbase.h>
#include <cwchar>

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

