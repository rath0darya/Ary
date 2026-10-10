package com.arywatcher.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String APP_URL = "http://127.0.0.1:8787/";
    private final ExecutorService pythonExecutor = Executors.newSingleThreadExecutor();
    private final ExecutorService readinessExecutor = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView webView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private WebChromeClient chromeClient;
    private FrameLayout root;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setSupportZoom(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        webView.setBackgroundColor(0xFF06070B);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    Toast.makeText(MainActivity.this, "Starting the local Python service…", Toast.LENGTH_SHORT).show();
                }
            }
        });
        chromeClient = new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                root.removeView(webView);
                root.addView(view, new FrameLayout.LayoutParams(-1, -1));
                getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION |
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE |
                    View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
            }

            @Override
            public void onHideCustomView() {
                if (customView == null) return;
                root.removeView(customView);
                customView = null;
                root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
                getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
                if (customViewCallback != null) customViewCallback.onCustomViewHidden();
                customViewCallback = null;
            }
        };
        webView.setWebChromeClient(chromeClient);
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                String filename = fileNameFromDisposition(contentDisposition);
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                request.setTitle(filename);
                request.setDescription("ARY Episode Watcher download");
                request.setMimeType(mimeType);
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null) request.addRequestHeader("Cookie", cookie);
                if (userAgent != null) request.addRequestHeader("User-Agent", userAgent);
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
                DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                manager.enqueue(request);
                Toast.makeText(this, "Download started. Check Android Downloads.", Toast.LENGTH_LONG).show();
            } catch (Exception ex) {
                Toast.makeText(this, "Could not start download: " + ex.getMessage(), Toast.LENGTH_LONG).show();
            }
        });

        try {
            copyAssetTree("runtime", new File(getFilesDir(), "runtime"));
        } catch (IOException e) {
            Toast.makeText(this, "Could not prepare bundled website: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }

        pythonExecutor.execute(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                Python.getInstance().getModule("android_entry").callAttr(
                    "start_server", new File(getFilesDir(), "runtime").getAbsolutePath());
            } catch (Throwable error) {
                android.util.Log.e("ARY-APK", "Embedded Python backend failed", error);
                handler.post(() -> Toast.makeText(this, "Python backend failed to start. Check app logs.", Toast.LENGTH_LONG).show());
            }
        });

        readinessExecutor.execute(() -> {
            for (int i = 0; i < 80; i++) {
                if (isBackendReady()) {
                    handler.post(() -> webView.loadUrl(APP_URL));
                    return;
                }
                try {
                    Thread.sleep(250);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
            handler.post(() -> Toast.makeText(this, "Python service did not start. Please reopen the app.", Toast.LENGTH_LONG).show());
        });
    }

    private boolean isBackendReady() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(APP_URL + "api/health").openConnection();
            connection.setConnectTimeout(500);
            connection.setReadTimeout(500);
            connection.setRequestMethod("GET");
            return connection.getResponseCode() == 200;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void copyAssetTree(String assetPath, File destination) throws IOException {
        String[] children = getAssets().list(assetPath);
        if (children == null || children.length == 0) {
            File parent = destination.getParentFile();
            if (parent != null) parent.mkdirs();
            try (InputStream input = getAssets().open(assetPath);
                 FileOutputStream output = new FileOutputStream(destination)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
            return;
        }
        if (!destination.exists() && !destination.mkdirs()) throw new IOException("Cannot create " + destination);
        for (String child : children) copyAssetTree(assetPath + "/" + child, new File(destination, child));
    }

    private String fileNameFromDisposition(String disposition) {
        if (disposition != null) {
            int at = disposition.toLowerCase().indexOf("filename=");
            if (at >= 0) {
                String name = disposition.substring(at + 9).trim().replace("\"", "");
                int semicolon = name.indexOf(';');
                if (semicolon >= 0) name = name.substring(0, semicolon);
                name = name.replaceAll("[\\\\/:*?<>|]", "-").trim();
                if (!name.isEmpty()) return name;
            }
        }
        return "ary-episode-" + System.currentTimeMillis() + ".mp4";
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            chromeClient.onHideCustomView();
        } else if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        pythonExecutor.shutdownNow();
        readinessExecutor.shutdownNow();
        super.onDestroy();
    }
}
