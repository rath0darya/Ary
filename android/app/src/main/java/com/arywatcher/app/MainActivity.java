package com.arywatcher.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.app.PictureInPictureParams;
import android.content.Context;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.media.AudioManager;
import android.provider.Settings;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.util.Rational;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

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
    private volatile boolean appFullscreen = false;
    private volatile boolean pipAutoEnter = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        root = new FrameLayout(this);
        root.setBackgroundColor(0xFF05060A);
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            int barsAndCutout = WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout();
            Insets insets = windowInsets.getInsets(barsAndCutout);
            if (appFullscreen || customView != null) root.setPadding(0, 0, 0, 0);
            else root.setPadding(insets.left, insets.top, insets.right, insets.bottom);
            return new WindowInsetsCompat.Builder(windowInsets).setInsets(barsAndCutout, Insets.NONE).build();
        });
        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setSupportZoom(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        webView.addJavascriptInterface(new AndroidBridge(), "AndroidBridge");
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
                setImmersiveFullscreen(true);
            }

            @Override
            public void onHideCustomView() {
                if (customView == null) return;
                root.removeView(customView);
                customView = null;
                root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
                setImmersiveFullscreen(false);
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
                request.setDescription("CineWave download");
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
                android.util.Log.e("CineWave", "Embedded Python backend startup returned an error", error);
                // Health polling below determines whether the local service is actually unavailable.
                // Avoid a false failure toast if the server is already responding.
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

    private void setImmersiveFullscreen(boolean enabled) {
        appFullscreen = enabled;
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        controller.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        View decor = getWindow().getDecorView();
        decor.setSystemUiVisibility(decor.getSystemUiVisibility() & ~(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR));
        if (enabled) {
            controller.hide(WindowInsetsCompat.Type.systemBars());
            if (root != null) root.setPadding(0, 0, 0, 0);
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars());
            if (root != null) ViewCompat.requestApplyInsets(root);
        }
    }

    private void updatePipParams(boolean autoEnter) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        try {
            PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder()
                .setAspectRatio(new Rational(16, 9));
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) builder.setAutoEnterEnabled(autoEnter);
            setPictureInPictureParams(builder.build());
        } catch (Exception error) {
            android.util.Log.w("CineWave", "Could not update PiP parameters", error);
        }
    }

    private void enterPipMode() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            Toast.makeText(this, "Picture-in-picture requires Android 8.0 or newer.", Toast.LENGTH_LONG).show();
            return;
        }
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            Toast.makeText(this, "Picture-in-picture is not supported on this device.", Toast.LENGTH_LONG).show();
            return;
        }
        try {
            updatePipParams(false);
            enterPictureInPictureMode(new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9)).build());
        } catch (Exception error) {
            android.util.Log.e("CineWave", "Could not enter picture-in-picture", error);
            Toast.makeText(this, "Could not start picture-in-picture. Check Android app settings.", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && Build.VERSION.SDK_INT < Build.VERSION_CODES.S && pipAutoEnter) {
            enterPipMode();
        }
    }

    @Override
    public void onPictureInPictureModeChanged(boolean isInPictureInPictureMode, android.content.res.Configuration newConfig) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig);
        if (webView != null) {
            String script = "document.getElementById('player')?.classList." +
                (isInPictureInPictureMode ? "add" : "remove") + "('pip-mode')";
            webView.evaluateJavascript(script, null);
        }
        if (isInPictureInPictureMode) {
            appFullscreen = true;
        } else {
            appFullscreen = false;
            if (root != null) ViewCompat.requestApplyInsets(root);
        }
    }

    private final class AndroidBridge {
        @JavascriptInterface public void enterPictureInPicture() {
            runOnUiThread(() -> enterPipMode());
        }
        @JavascriptInterface public void setPipAutoEnter(boolean enabled) {
            pipAutoEnter = enabled;
            runOnUiThread(() -> updatePipParams(enabled));
        }
        @JavascriptInterface public void setFullscreen(boolean enabled) {
            runOnUiThread(() -> setImmersiveFullscreen(enabled));
        }
        @JavascriptInterface public void setOrientation(boolean landscape) {
            runOnUiThread(() -> setRequestedOrientation(landscape ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE : ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED));
        }
        @JavascriptInterface public int getMediaVolume() {
            AudioManager audio = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            int maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
            return maximum <= 0 ? 0 : Math.round(audio.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / maximum);
        }
        @JavascriptInterface public void setMediaVolume(int percent) {
            runOnUiThread(() -> {
                AudioManager audio = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
                int maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
                int target = Math.round(Math.max(0, Math.min(100, percent)) * maximum / 100f);
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0);
            });
        }
        @JavascriptInterface public float getScreenBrightness() {
            float current = getWindow().getAttributes().screenBrightness;
            if (current >= 0f) return Math.max(0.15f, Math.min(1f, current));
            try { return Math.max(0.15f, Math.min(1f, Settings.System.getInt(getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, 200) / 255f)); }
            catch (Exception ignored) { return 0.8f; }
        }
        @JavascriptInterface public void setScreenBrightness(float level) {
            runOnUiThread(() -> {
                WindowManager.LayoutParams attributes = getWindow().getAttributes();
                attributes.screenBrightness = Math.max(0.15f, Math.min(1f, level));
                getWindow().setAttributes(attributes);
            });
        }
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
        return "cinewave-episode-" + System.currentTimeMillis() + ".mp4";
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
