// Minimal Direct2D image viewer for fast scaled drawing.
// Build (MinGW):
//   g++ -std=c++17 -O2 -municode -o build/image_viewer_d2d.exe image_viewer_d2d.cpp \
//       -ld2d1 -ldwrite -lwindowscodecs -lole32 -loleaut32 -lgdi32 -luuid

#include <windows.h>
#include <d2d1.h>
#include <dwrite.h>
#include <wincodec.h>
#include <objbase.h>
#include <shellapi.h>
#include <string>

#pragma comment(lib, "d2d1")
#pragma comment(lib, "dwrite")
#pragma comment(lib, "windowscodecs")

template <class T> inline void SafeRelease(T** ppT) {
    if (*ppT) { (*ppT)->Release(); *ppT = nullptr; }
}

struct AppCtx {
    std::wstring imagePath;
    ID2D1Factory* d2dFactory = nullptr;
    IWICImagingFactory* wicFactory = nullptr;
    ID2D1HwndRenderTarget* rt = nullptr;
    ID2D1Bitmap* bmp = nullptr;
    UINT imgW = 0, imgH = 0;
};

static void LoadImageToBitmap(AppCtx* ctx) {
    if (!ctx || ctx->imagePath.empty()) return;
    SafeRelease(&ctx->bmp);

    IWICBitmapDecoder* decoder = nullptr;
    HRESULT hr = ctx->wicFactory->CreateDecoderFromFilename(
        ctx->imagePath.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
    if (FAILED(hr)) return;

    IWICBitmapFrameDecode* frame = nullptr;
    hr = decoder->GetFrame(0, &frame);
    if (FAILED(hr)) { SafeRelease(&decoder); return; }

    frame->GetSize(&ctx->imgW, &ctx->imgH);

    IWICFormatConverter* conv = nullptr;
    hr = ctx->wicFactory->CreateFormatConverter(&conv);
    if (SUCCEEDED(hr)) {
        hr = conv->Initialize(frame, GUID_WICPixelFormat32bppPBGRA,
                              WICBitmapDitherTypeNone, nullptr, 0.0f, WICBitmapPaletteTypeCustom);
    }
    if (SUCCEEDED(hr)) {
        hr = ctx->rt->CreateBitmapFromWicBitmap(conv, nullptr, &ctx->bmp);
    }
    SafeRelease(&conv);
    SafeRelease(&frame);
    SafeRelease(&decoder);
}

static void EnsureRenderTarget(AppCtx* ctx, HWND hwnd) {
    if (!ctx->rt) {
        RECT rc; GetClientRect(hwnd, &rc);
        D2D1_SIZE_U size = D2D1::SizeU(rc.right - rc.left, rc.bottom - rc.top);
        ctx->d2dFactory->CreateHwndRenderTarget(
            D2D1::RenderTargetProperties(),
            D2D1::HwndRenderTargetProperties(hwnd, size),
            &ctx->rt);
        if (ctx->rt) {
            LoadImageToBitmap(ctx);
        }
    }
}

static void OnPaint(AppCtx* ctx, HWND hwnd) {
    PAINTSTRUCT ps; BeginPaint(hwnd, &ps);
    if (!ctx->rt) { EndPaint(hwnd, &ps); return; }
    ctx->rt->BeginDraw();
    ctx->rt->Clear(D2D1::ColorF(0.09f, 0.09f, 0.09f));
    if (ctx->bmp) {
        D2D1_SIZE_F bs = ctx->bmp->GetSize();
        RECT rc; GetClientRect(hwnd, &rc);
        float W = (float)(rc.right - rc.left);
        float H = (float)(rc.bottom - rc.top);
        float sx = W / bs.width;
        float sy = H / bs.height;
        float s = (sx < sy ? sx : sy);
        if (s > 1.0f) s = 1.0f; // no upscale by default
        float w = bs.width * s;
        float h = bs.height * s;
        float x = (W - w) * 0.5f;
        float y = (H - h) * 0.5f;
        D2D1_RECT_F dst = D2D1::RectF(x, y, x + w, y + h);
        ctx->rt->DrawBitmap(ctx->bmp, dst, 1.0f, D2D1_BITMAP_INTERPOLATION_MODE_LINEAR);
    }
    HRESULT hr = ctx->rt->EndDraw();
    if (hr == D2DERR_RECREATE_TARGET) {
        SafeRelease(&ctx->bmp);
        SafeRelease(&ctx->rt);
    }
    EndPaint(hwnd, &ps);
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    AppCtx* ctx = reinterpret_cast<AppCtx*>(GetWindowLongPtr(hwnd, GWLP_USERDATA));
    switch (msg) {
    case WM_CREATE: return 0;
    case WM_SIZE:
        if (ctx && ctx->rt) {
            UINT w = LOWORD(lParam), h = HIWORD(lParam);
            ctx->rt->Resize(D2D1::SizeU(w, h));
        }
        return 0;
    case WM_PAINT:
        if (ctx) OnPaint(ctx, hwnd);
        return 0;
    case WM_KEYDOWN:
        if (wParam == VK_ESCAPE) { DestroyWindow(hwnd); }
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

int APIENTRY wWinMain(HINSTANCE hInst, HINSTANCE, LPWSTR, int nCmdShow) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::wstring path;
    if (argv && argc >= 2) {
        path = argv[1];
    }
    if (argv) LocalFree(argv);
    if (path.empty()) return 2;

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    bool co_inited = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;

    AppCtx ctx;
    ctx.imagePath = path;
    D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &ctx.d2dFactory);
    CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&ctx.wicFactory));

    const wchar_t* CLASS_NAME = L"D2DImageViewerWindow";
    WNDCLASS wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInst;
    wc.lpszClassName = CLASS_NAME;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    RegisterClass(&wc);

    HWND hwnd = CreateWindowEx(
        WS_EX_APPWINDOW, CLASS_NAME, L"Image Viewer",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 1200, 800,
        nullptr, nullptr, hInst, nullptr);
    if (!hwnd) return 3;

    SetWindowLongPtr(hwnd, GWLP_USERDATA, (LONG_PTR)&ctx);
    EnsureRenderTarget(&ctx, hwnd);
    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
        if (!ctx.rt) EnsureRenderTarget(&ctx, hwnd);
    }

    SafeRelease(&ctx.bmp);
    SafeRelease(&ctx.rt);
    SafeRelease(&ctx.d2dFactory);
    SafeRelease(&ctx.wicFactory);
    if (co_inited) CoUninitialize();
    return 0;
}

