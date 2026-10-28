package com.arywatcher.app;

import android.app.Activity;
import android.media.AudioManager;
import android.app.DownloadManager;
import android.app.PictureInPictureParams;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.util.Rational;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.PopupMenu;
import android.widget.SeekBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.util.LruCache;

import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.media3.common.MediaItem;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.Format;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.TrackSelectionOverride;
import androidx.media3.common.TrackSelectionParameters;
import androidx.media3.common.Tracks;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.ui.PlayerView;
import androidx.media3.ui.AspectRatioFrameLayout;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Native CineWave app. The embedded Python process supplies catalogue and stream APIs only. */
@UnstableApi
public class MainActivity extends Activity {
    private static final String APP_URL = "http://127.0.0.1:8787/";
    private static final int BG = Color.rgb(8, 10, 15);
    private static final int SURFACE = Color.rgb(19, 23, 32);
    private static final int SURFACE_2 = Color.rgb(27, 32, 43);
    private static final int TEXT = Color.rgb(246, 247, 250);
    private static final int MUTED = Color.rgb(159, 168, 184);
    private static final int ACCENT = Color.rgb(246, 39, 78);
    private static final String[] CATEGORIES = {"All", "Drama", "Telefilms", "Shows", "Movies", "Series", "Comedy", "Romance", "Action", "Sports", "News"};

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService backendExecutor = Executors.newSingleThreadExecutor();
    private final ExecutorService apiExecutor = Executors.newFixedThreadPool(4);
    private final ExecutorService imageExecutor = Executors.newFixedThreadPool(3);
    private FrameLayout root;
    private LinearLayout screen;
    private LinearLayout content;
    private LinearLayout episodeList;
    private EditText search;
    private TextView status;
    private GridLayout seriesGrid;
    private LinearLayout categoryChips;
    private final LruCache<String, Bitmap> imageCache = new LruCache<String, Bitmap>(8 * 1024 * 1024) {
        @Override protected int sizeOf(String key, Bitmap value) { return value.getByteCount(); }
    };
    private JSONArray allSeries = new JSONArray();
    private JSONArray episodes = new JSONArray();
    private JSONObject currentSeries;
    private String selectedCategory = "All";
    private String searchQuery = "";
    private String currentSeriesId = "";
    private String currentSeriesTitle = "";
    private ExoPlayer player;
    private PlayerView playerView;
    private Runnable downloadPoll;
    private Runnable searchUpdate;
    private Runnable cataloguePoll;
    private Runnable episodePoll;
    private Runnable episodeRefreshCheck;
    private Runnable playerProgressUpdate;
    private String discoveryState = "idle";
    private String discoveryError = "";
    private String episodeDiscoveryState = "idle";
    private TextView episodeStatus;
    private TextView playbackTime;
    private TextView playbackDuration;
    private TextView playerPlayButton;
    private Button playerPipButton;
    private Button playerLockButton;
    private SeekBar playbackSeek;
    private FrameLayout playerStage;
    private Runnable playerHideControls;
    private JSONObject playingEpisode;
    private boolean playerControlsVisible = true;
    private boolean playerControlsLocked = false;
    private boolean fullscreen = false;
    private boolean userSeeking = false;
    private long lastPlayerTap = 0L;
    private long lastPlaybackSaveAt = 0L;
    private float playbackSpeed = 1f;
    private boolean repeatCurrent = false;
    private float gestureStartX;
    private float gestureStartY;
    private float gestureInitialBrightness = -1f;
    private int gestureInitialVolume;
    private boolean gestureAdjusting = false;
    private boolean gestureSeeking = false;
    private long gestureSeekDelta = 0L;
    private boolean gestureBrightness = false;
    private TextView playerGestureFeedback;
    private String pendingExportUrl = "";
    private String pendingExportTitle = "";

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return insets;
        });
        setContentView(root);
        showLoading("Starting CineWave", "Preparing the on-device media service…");
        startBackend();
    }

    private void startBackend() {
        backendExecutor.execute(() -> {
            try {
                File runtime = new File(getFilesDir(), "runtime");
                copyAssetTree("runtime", runtime);
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                Python.getInstance().getModule("android_entry").callAttr("start_server", runtime.getAbsolutePath());
            } catch (Throwable error) {
                android.util.Log.e("CineWave", "Embedded media service failed to start", error);
            }
        });
        apiExecutor.execute(() -> {
            for (int attempt = 0; attempt < 120; attempt++) {
                try {
                    JSONObject health = requestJson("api/health");
                    if (health.optBoolean("ok")) {
                        main.post(this::loadCatalogue);
                        return;
                    }
                } catch (Exception ignored) { }
                try { Thread.sleep(250); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); return; }
            }
            main.post(() -> showError("The media service did not start", "Check the app installation, then try again.", this::startBackend));
        });
    }

    private void showLoading(String title, String message) {
        root.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(28), dp(28), dp(28), dp(28));
        TextView brand = text("CINE•WAVE", 25, TEXT, true);
        brand.setLetterSpacing(.04f);
        TextView heading = text(title, 20, TEXT, true);
        heading.setGravity(Gravity.CENTER);
        TextView detail = text(message, 14, MUTED, false);
        detail.setGravity(Gravity.CENTER);
        ProgressBar spinner = new ProgressBar(this);
        LinearLayout.LayoutParams spinnerParams = new LinearLayout.LayoutParams(dp(38), dp(38));
        spinnerParams.setMargins(0, dp(24), 0, dp(22));
        box.addView(brand);
        box.addView(spinner, spinnerParams);
        box.addView(heading);
        LinearLayout.LayoutParams detailParams = new LinearLayout.LayoutParams(-1, -2);
        detailParams.setMargins(0, dp(8), 0, 0);
        box.addView(detail, detailParams);
        root.addView(box, new FrameLayout.LayoutParams(-1, -1));
    }

    private void showError(String title, String message, Runnable retry) {
        root.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(28), dp(28), dp(28), dp(28));
        TextView heading = text(title, 21, TEXT, true);
        heading.setGravity(Gravity.CENTER);
        TextView body = text(message, 14, MUTED, false);
        body.setGravity(Gravity.CENTER);
        Button button = button("Try again", true);
        button.setOnClickListener(v -> { showLoading("Starting CineWave", "Reconnecting to the on-device media service…"); retry.run(); });
        box.addView(heading);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.setMargins(0, dp(12), 0, dp(20));
        box.addView(body, p);
        box.addView(button);
        root.addView(box, new FrameLayout.LayoutParams(-1, -1));
    }

    private void loadCatalogue() {
        loadCatalogue(false);
    }

    private void loadCatalogue(boolean refresh) {
        if (allSeries.length() == 0) showLoading("Loading your collection", "Starting the on-device catalogue…");
        api("api/series" + (refresh ? "?refresh=1" : ""), result -> {
            allSeries = result.optJSONArray("series");
            if (allSeries == null) allSeries = new JSONArray();
            JSONObject discovery = result.optJSONObject("discovery");
            if (discovery != null) {
                discoveryState = discovery.optString("state", "idle");
                discoveryError = discovery.optString("error", "");
            }
            showHome();
            scheduleCataloguePoll();
        }, error -> showError("Couldn’t load the catalogue", error, () -> loadCatalogue(refresh)));
    }

    private void scheduleCataloguePoll() {
        if (cataloguePoll != null) main.removeCallbacks(cataloguePoll);
        if (!"running".equals(discoveryState)) return;
        cataloguePoll = () -> api("api/series", result -> {
            JSONArray updated = result.optJSONArray("series");
            JSONObject discovery = result.optJSONObject("discovery");
            if (discovery != null) {
                discoveryState = discovery.optString("state", "idle");
                discoveryError = discovery.optString("error", "");
            }
            if (updated != null && !updated.toString().equals(allSeries.toString())) {
                allSeries = updated;
                if (seriesGrid != null) renderSeriesGrid(seriesGrid, searchQuery);
            }
            if (status != null) renderSeriesGrid(seriesGrid, searchQuery);
            scheduleCataloguePoll();
        }, error -> {
            cataloguePoll = this::loadCatalogue;
            main.postDelayed(cataloguePoll, 5000);
        });
        main.postDelayed(cataloguePoll, 4000);
    }

    private void showHome() {
        if (episodePoll != null) main.removeCallbacks(episodePoll);
        if (episodeRefreshCheck != null) main.removeCallbacks(episodeRefreshCheck);
        currentSeries = null;
        currentSeriesId = "";
        currentSeriesTitle = "";
        installBaseScreen("Your collection", true);
        LinearLayout toolbar = row();
        TextView title = text("CINE•WAVE", 24, TEXT, true);
        title.setLetterSpacing(.03f);
        Button refresh = button("Refresh", false);
        refresh.setOnClickListener(v -> loadCatalogue(true));
        toolbar.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        toolbar.addView(refresh);
        content.addView(toolbar);
        TextView subtitle = text("Find your next watch", 14, MUTED, false);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(-1, -2);
        subtitleParams.setMargins(0, dp(6), 0, dp(16));
        content.addView(subtitle, subtitleParams);

        search = new EditText(this);
        search.setSingleLine(true);
        search.setTextColor(TEXT);
        search.setHintTextColor(MUTED);
        search.setHint("Search titles, cast, genres…");
        search.setTextSize(15);
        search.setPadding(dp(16), 0, dp(16), 0);
        search.setBackground(rounded(SURFACE_2, 15, 0));
        LinearLayout.LayoutParams searchParams = new LinearLayout.LayoutParams(-1, dp(52));
        searchParams.setMargins(0, 0, 0, dp(14));
        content.addView(search, searchParams);
        search.setText(searchQuery);

        HorizontalScrollView categories = new HorizontalScrollView(this);
        categories.setHorizontalScrollBarEnabled(false);
        categoryChips = row();
        categoryChips.setPadding(0, 0, dp(4), dp(14));
        for (String category : CATEGORIES) {
            Button chip = button(category, category.equals(selectedCategory));
            chip.setTag(category);
            chip.setMinHeight(dp(40));
            LinearLayout.LayoutParams chipParams = new LinearLayout.LayoutParams(-2, dp(40));
            chipParams.setMargins(0, 0, dp(8), 0);
            categoryChips.addView(chip, chipParams);
            chip.setOnClickListener(v -> {
                selectedCategory = category;
                updateCategoryStyles();
                renderSeriesGrid(seriesGrid, searchQuery);
            });
        }
        categories.addView(categoryChips);
        content.addView(categories);

        status = text("", 12, MUTED, false);
        content.addView(status);
        seriesGrid = new GridLayout(this);
        seriesGrid.setColumnCount(2);
        content.addView(seriesGrid, new LinearLayout.LayoutParams(-1, -2));
        renderSeriesGrid(seriesGrid, searchQuery);
        search.addTextChangedListener(new android.text.TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                searchQuery = s.toString();
                if (searchUpdate != null) main.removeCallbacks(searchUpdate);
                searchUpdate = () -> renderSeriesGrid(seriesGrid, searchQuery);
                main.postDelayed(searchUpdate, 180);
            }
            @Override public void afterTextChanged(android.text.Editable s) { }
        });
    }

    private void updateCategoryStyles() {
        if (categoryChips == null) return;
        for (int i = 0; i < categoryChips.getChildCount(); i++) {
            View child = categoryChips.getChildAt(i);
            if (!(child instanceof Button)) continue;
            Button chip = (Button) child;
            boolean active = selectedCategory.equals(chip.getTag());
            chip.setBackground(rounded(active ? ACCENT : SURFACE_2, 13, active ? 0 : Color.rgb(50, 57, 72)));
            chip.setTextColor(active ? Color.WHITE : TEXT);
        }
    }

    private void renderSeriesGrid(GridLayout grid, String query) {
        grid.removeAllViews();
        String needle = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        List<JSONObject> matches = new ArrayList<>();
        for (int i = 0; i < allSeries.length(); i++) {
            JSONObject item = allSeries.optJSONObject(i);
            if (item == null || !matchesCategory(item, selectedCategory)) continue;
            String haystack = (item.optString("title") + " " + item.optString("id") + " " + item.optString("description") + " " + item.optString("cast") + " " + item.optString("year") + " " + item.optString("content_type") + " " + genres(item)).toLowerCase(Locale.ROOT);
            if (needle.isEmpty() || haystack.contains(needle)) matches.add(item);
        }
        if (status != null) {
            String label = matches.size() + (matches.size() == 1 ? " title" : " titles");
            if ("running".equals(discoveryState)) label += " · updating catalogue…";
            else if ("error".equals(discoveryState) && !discoveryError.isEmpty()) label += " · update paused; refresh to retry";
            status.setText(label);
        }
        if (matches.isEmpty()) {
            TextView empty = text("No titles match. Try another search or category.", 14, MUTED, false);
            empty.setPadding(dp(8), dp(28), dp(8), dp(28));
            grid.addView(empty, new GridLayout.LayoutParams(GridLayout.spec(0, 1), GridLayout.spec(0, 2)));
            return;
        }
        int visibleCount = Math.min(matches.size(), 80);
        if (matches.size() > visibleCount && status != null) status.setText("Showing " + visibleCount + " of " + matches.size() + " titles · search to narrow");
        for (int i = 0; i < visibleCount; i++) {
            final JSONObject item = matches.get(i);
            int column = i % 2;
            int rowIndex = i / 2;
            View card = seriesCard(item);
            GridLayout.LayoutParams params = new GridLayout.LayoutParams(GridLayout.spec(rowIndex, 1f), GridLayout.spec(column, 1f));
            params.width = 0;
            params.setMargins(dp(5), dp(5), dp(5), dp(10));
            grid.addView(card, params);
        }
    }

    private View seriesCard(JSONObject item) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(10), dp(10), dp(10), dp(12));
        card.setBackground(rounded(SURFACE, 18, Color.rgb(39, 45, 59)));
        card.setClickable(true);
        card.setFocusable(true);
        ImageView poster = new ImageView(this);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        poster.setBackground(rounded(SURFACE_2, 13, 0));
        poster.setContentDescription(item.optString("title", "Title") + " artwork");
        LinearLayout.LayoutParams imageParams = new LinearLayout.LayoutParams(-1, dp(190));
        card.addView(poster, imageParams);
        loadImage(posterOf(item), poster);
        TextView name = text(item.optString("title", "Untitled"), 15, TEXT, true);
        name.setMaxLines(2);
        name.setEllipsize(android.text.TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams nameParams = new LinearLayout.LayoutParams(-1, -2);
        nameParams.setMargins(0, dp(10), 0, dp(4));
        card.addView(name, nameParams);
        String meta = item.optString("content_type", "Series");
        if (!item.optString("year").isEmpty()) meta += " · " + item.optString("year");
        TextView details = text(meta, 11, MUTED, false);
        details.setMaxLines(1);
        details.setEllipsize(android.text.TextUtils.TruncateAt.END);
        card.addView(details);
        card.setOnClickListener(v -> openSeries(item));
        return card;
    }

    private void openSeries(JSONObject series) {
        if (episodePoll != null) main.removeCallbacks(episodePoll);
        if (episodeRefreshCheck != null) main.removeCallbacks(episodeRefreshCheck);
        currentSeries = series;
        currentSeriesId = series.optString("id", "");
        currentSeriesTitle = series.optString("title", "Untitled");
        showLoading("Opening " + currentSeriesTitle, "Loading episodes…");
        api("api/series/detail?series=" + enc(currentSeriesId), result -> {
            currentSeries = result.optJSONObject("series");
            if (currentSeries == null) currentSeries = series;
            showSeriesDetails();
            loadEpisodes(false);
        }, error -> showError("Couldn’t open this title", error, () -> openSeries(series)));
    }

    private void showSeriesDetails() {
        installBaseScreen(currentSeriesTitle, true);
        LinearLayout toolbar = row();
        Button back = button("‹", false);
        back.setContentDescription("Back to catalogue");
        back.setOnClickListener(v -> showHome());
        TextView heading = text(currentSeriesTitle, 19, TEXT, true);
        heading.setMaxLines(1);
        heading.setEllipsize(android.text.TextUtils.TruncateAt.END);
        toolbar.addView(back);
        LinearLayout.LayoutParams headingParams = new LinearLayout.LayoutParams(0, -2, 1);
        headingParams.setMargins(dp(12), 0, 0, 0);
        toolbar.addView(heading, headingParams);
        content.addView(toolbar);

        ScrollView scroll = new ScrollView(this);
        LinearLayout detail = new LinearLayout(this);
        detail.setOrientation(LinearLayout.VERTICAL);
        detail.setPadding(0, dp(12), 0, dp(24));
        ImageView artwork = new ImageView(this);
        artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        artwork.setBackground(rounded(SURFACE_2, 18, 0));
        LinearLayout.LayoutParams artworkParams = new LinearLayout.LayoutParams(-1, dp(230));
        detail.addView(artwork, artworkParams);
        String art = posterOf(currentSeries);
        loadImage(art, artwork);
        TextView title = text(currentSeriesTitle, 28, TEXT, true);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-1, -2);
        titleParams.setMargins(0, dp(16), 0, dp(8));
        detail.addView(title, titleParams);
        String meta = currentSeries.optString("content_type", "Series");
        if (!currentSeries.optString("year").isEmpty()) meta += " · " + currentSeries.optString("year");
        if (!genres(currentSeries).isEmpty()) meta += " · " + genres(currentSeries);
        detail.addView(text(meta, 13, MUTED, false));
        String description = currentSeries.optString("description", "").trim();
        if (!description.isEmpty()) {
            TextView synopsis = text(description, 14, Color.rgb(202, 207, 217), false);
            synopsis.setLineSpacing(dp(3), 1f);
            LinearLayout.LayoutParams synopsisParams = new LinearLayout.LayoutParams(-1, -2);
            synopsisParams.setMargins(0, dp(12), 0, dp(18));
            detail.addView(synopsis, synopsisParams);
        }
        LinearLayout episodeHead = row();
        TextView episodesTitle = text("Episodes and videos", 19, TEXT, true);
        episodeHead.addView(episodesTitle, new LinearLayout.LayoutParams(0, -2, 1));
        Button refresh = button("Find new episodes", false);
        refresh.setOnClickListener(v -> loadEpisodes(true));
        episodeHead.addView(refresh);
        LinearLayout.LayoutParams episodeHeadParams = new LinearLayout.LayoutParams(-1, -2);
        episodeHeadParams.setMargins(0, dp(18), 0, dp(8));
        detail.addView(episodeHead, episodeHeadParams);
        episodeList = new LinearLayout(this);
        episodeList.setOrientation(LinearLayout.VERTICAL);
        episodeStatus = text("", 12, MUTED, false);
        episodeList.addView(episodeStatus);
        detail.addView(episodeList);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        scroll.addView(detail);
        episodeList.addView(text("Loading episodes…", 13, MUTED, false));
    }

    private void loadEpisodes(boolean refresh) {
        if (currentSeriesId.isEmpty()) return;
        String path = "api/episodes?series=" + enc(currentSeriesId) + (refresh ? "&refresh=1" : "");
        api(path, result -> {
            episodes = result.optJSONArray("episodes");
            if (episodes == null) episodes = new JSONArray();
            JSONObject discovery = result.optJSONObject("discovery");
            if (discovery != null) episodeDiscoveryState = discovery.optString("state", "idle");
            renderEpisodes(refresh);
            scheduleEpisodePoll();
            if (!"running".equals(episodeDiscoveryState)) scheduleEpisodeRefreshCheck();
        }, error -> showToast("Episodes unavailable: " + error));
    }

    private void scheduleEpisodePoll() {
        if (episodePoll != null) main.removeCallbacks(episodePoll);
        if (!"running".equals(episodeDiscoveryState)) return;
        episodePoll = () -> api("api/episodes?series=" + enc(currentSeriesId), result -> {
            JSONArray updated = result.optJSONArray("episodes");
            JSONObject discovery = result.optJSONObject("discovery");
            if (discovery != null) episodeDiscoveryState = discovery.optString("state", "idle");
            if (updated != null && !updated.toString().equals(episodes.toString())) episodes = updated;
            renderEpisodes(false);
            scheduleEpisodePoll();
            if (!"running".equals(episodeDiscoveryState)) scheduleEpisodeRefreshCheck();
        }, error -> {
            episodePoll = () -> loadEpisodes(false);
            main.postDelayed(episodePoll, 6000);
        });
        main.postDelayed(episodePoll, 2500);
    }

    private void scheduleEpisodeRefreshCheck() {
        if (episodeRefreshCheck != null) main.removeCallbacks(episodeRefreshCheck);
        if (currentSeriesId.isEmpty()) return;
        episodeRefreshCheck = () -> loadEpisodes(false);
        main.postDelayed(episodeRefreshCheck, 5 * 60 * 1000L);
    }

    private void renderEpisodes(boolean refresh) {
        if (episodeList == null) return;
        episodeList.removeAllViews();
        if (episodeStatus != null) episodeList.addView(episodeStatus);
        if (episodeStatus != null) {
            episodeStatus.setText("running".equals(episodeDiscoveryState)
                ? "Checking ARY+ VOD for newly released episodes…"
                : "error".equals(episodeDiscoveryState)
                    ? "ARY+ VOD discovery failed. Pull to refresh and try again."
                    : "completed".equals(episodeDiscoveryState)
                        ? "VOD catalogue checked · " + episodes.length() + " episodes"
                        : "");
        }
        if (refresh && "completed".equals(episodeDiscoveryState)) showToast("Episode guide updated");
        if (episodes.length() == 0) {
            TextView empty = text("No episodes are listed for this title yet.", 14, MUTED, false);
            episodeList.addView(empty);
            return;
        }
        for (int i = episodes.length() - 1; i >= 0; i--) {
            JSONObject episode = episodes.optJSONObject(i);
            if (episode == null) continue;
            episodeList.addView(episodeCard(episode));
        }
    }

    private View episodeCard(JSONObject episode) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(13), dp(14), dp(13));
        card.setBackground(rounded(SURFACE, 16, Color.rgb(39, 45, 59)));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(-1, -2);
        cardParams.setMargins(0, dp(6), 0, dp(6));
        String number = episode.optString("number", "");
        String title = episode.optString("title", "Episode");
        LinearLayout summary = row();
        String episodeArtwork = episode.optString("image", episode.optString("image_url", episode.optString("thumbnail", "")));
        if (!episodeArtwork.isEmpty()) {
            ImageView thumbnail = new ImageView(this);
            thumbnail.setScaleType(ImageView.ScaleType.CENTER_CROP);
            thumbnail.setBackground(rounded(SURFACE_2, 10, 0));
            thumbnail.setContentDescription(title + " artwork");
            summary.addView(thumbnail, new LinearLayout.LayoutParams(dp(96), dp(64)));
            LinearLayout.LayoutParams thumbMargin = (LinearLayout.LayoutParams) thumbnail.getLayoutParams();
            thumbMargin.setMargins(0, 0, dp(12), 0);
            thumbnail.setLayoutParams(thumbMargin);
            loadImage(episodeArtwork, thumbnail);
        }
        LinearLayout summaryText = new LinearLayout(this);
        summaryText.setOrientation(LinearLayout.VERTICAL);
        TextView name = text((number.isEmpty() ? "" : "Episode " + number + " · ") + title, 15, TEXT, true);
        name.setMaxLines(2);
        summaryText.addView(name);
        if (!episode.optString("date").isEmpty()) {
            TextView date = text(episode.optString("date"), 12, MUTED, false);
            LinearLayout.LayoutParams dateParams = new LinearLayout.LayoutParams(-1, -2);
            dateParams.setMargins(0, dp(5), 0, 0);
            summaryText.addView(date, dateParams);
        }
        summary.addView(summaryText, new LinearLayout.LayoutParams(0, -2, 1));
        card.addView(summary);
        LinearLayout actions = row();
        Button play = button("▶  Play", true);
        play.setOnClickListener(v -> playEpisode(episode));
        Button download = button("↓  Download", false);
        download.setOnClickListener(v -> startDownload(episode, download));
        LinearLayout.LayoutParams actionParams = new LinearLayout.LayoutParams(0, dp(44), 1);
        actionParams.setMargins(0, dp(12), dp(8), 0);
        actions.addView(play, actionParams);
        LinearLayout.LayoutParams downloadParams = new LinearLayout.LayoutParams(0, dp(44), 1);
        downloadParams.setMargins(dp(8), dp(12), 0, 0);
        actions.addView(download, downloadParams);
        card.addView(actions);
        if (!hasDirectStream(episode)) {
            TextView sourceStatus = text("In-app VOD source not available yet", 11, MUTED, false);
            LinearLayout.LayoutParams sourceParams = new LinearLayout.LayoutParams(-1, -2);
            sourceParams.setMargins(0, dp(8), 0, 0);
            card.addView(sourceStatus, sourceParams);
        }
        return card;
    }

    private void playEpisode(JSONObject episode) {
        if (currentSeriesId.isEmpty()) return;
        String id = episode.optString("id", "");
        if (id.isEmpty()) { showToast("This video has no episode ID."); return; }
        String savedSource = directStreamSource(episode);
        if (!savedSource.isEmpty()) {
            startNativePlayer(savedSource, episode);
            return;
        }
        showLoading("Preparing playback", episode.optString("title", "Loading video…"));
        api("api/stream-info?series=" + enc(currentSeriesId) + "&episode=" + enc(id), result -> {
            JSONObject stream = result.optJSONObject("stream");
            String source = stream == null ? "" : stream.optString("source", "");
            if (source.isEmpty() && stream != null) {
                JSONObject best = stream.optJSONObject("best");
                if (best != null) source = best.optString("uri", "");
            }
            if (source.isEmpty()) { showToast("No direct stream is available for this episode."); showSeriesDetails(); renderEpisodes(false); return; }
            startNativePlayer(source, episode);
        }, error -> { showSeriesDetails(); renderEpisodes(false); showToast("Playback unavailable: " + error); });
    }

    private boolean hasDirectStream(JSONObject episode) {
        return !directStreamSource(episode).isEmpty();
    }

    private String directStreamSource(JSONObject episode) {
        if (episode == null || episode.isNull("stream")) return "";
        String source = episode.optString("stream", "").trim();
        if (source.isEmpty() || "null".equalsIgnoreCase(source)) return "";
        try {
            Uri uri = Uri.parse(source);
            String host = uri.getHost();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || host == null) return "";
            host = host.toLowerCase(Locale.ROOT);
            if (!host.equals("vod.aryzap.com") && !host.endsWith(".vod.aryzap.com")) return "";
            String path = uri.getPath() == null ? "" : uri.getPath().toLowerCase(Locale.ROOT);
            return path.endsWith(".m3u8") || path.endsWith(".mp4") ? source : "";
        } catch (Exception ignored) { return ""; }
    }

    private void startNativePlayer(String source, JSONObject episode) {
        releasePlayer();
        playingEpisode = episode;
        playerControlsVisible = true;
        playerControlsLocked = false;
        fullscreen = false;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        java.util.Map<String, String> streamHeaders = new java.util.HashMap<>();
        streamHeaders.put("Referer", "https://aryplus.tv/");
        streamHeaders.put("Origin", "https://aryplus.tv");
        DefaultHttpDataSource.Factory http = new DefaultHttpDataSource.Factory()
            .setUserAgent("Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 CineWave/2.0")
            .setDefaultRequestProperties(streamHeaders);
        player = new ExoPlayer.Builder(this)
            .setMediaSourceFactory(new DefaultMediaSourceFactory(http))
            .build();
        player.setAudioAttributes(AudioAttributes.DEFAULT, true);
        player.setPlaybackSpeed(playbackSpeed);
        player.setRepeatMode(repeatCurrent ? Player.REPEAT_MODE_ONE : Player.REPEAT_MODE_OFF);
        playerView = new PlayerView(this);
        playerView.setPlayer(player);
        playerView.setUseController(false);
        playerView.setShowBuffering(PlayerView.SHOW_BUFFERING_ALWAYS);
        playerView.setBackgroundColor(Color.BLACK);
        playerView.setContentDescription("Video player");
        playerView.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        player.addListener(new Player.Listener() {
            @Override public void onPlayerError(PlaybackException error) {
                showToast("Playback failed: " + error.getMessage());
            }
            @Override public void onIsPlayingChanged(boolean isPlaying) {
                if (playerPlayButton != null) playerPlayButton.setText(isPlaying ? "Ⅱ" : "▶");
                boolean videoPlaying = isVideoPlaying();
                if (playerPipButton != null) playerPipButton.setVisibility(videoPlaying ? View.VISIBLE : View.GONE);
                updatePictureInPictureParams(videoPlaying);
            }
            @Override public void onVideoSizeChanged(androidx.media3.common.VideoSize videoSize) {
                boolean videoPlaying = isVideoPlaying();
                if (playerPipButton != null) playerPipButton.setVisibility(videoPlaying ? View.VISIBLE : View.GONE);
                updatePictureInPictureParams(videoPlaying);
            }
            @Override public void onPlaybackStateChanged(int playbackState) {
                if (playbackState == Player.STATE_ENDED) {
                    clearSavedPlaybackPosition();
                    playNextEpisode();
                }
            }
        });
        long resumeAt = savedPlaybackPosition(episode);
        player.setMediaItem(MediaItem.fromUri(Uri.parse(source)), resumeAt);
        player.prepare();
        player.play();
        if (resumeAt > 5000) showToast("Resuming at " + formatPlayerTime(resumeAt));
        root.removeAllViews();
        FrameLayout stage = new FrameLayout(this);
        playerStage = stage;
        stage.setBackgroundColor(Color.BLACK);
        stage.addView(playerView, new FrameLayout.LayoutParams(-1, -1));
        playerView.addOnLayoutChangeListener((view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
            if (isVideoPlaying()) updatePictureInPictureParams(true);
        });
        final float[] videoZoom = {1f};
        ScaleGestureDetector zoomGesture = new ScaleGestureDetector(this, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override public boolean onScale(ScaleGestureDetector detector) {
                videoZoom[0] = Math.max(1f, Math.min(2.5f, videoZoom[0] * detector.getScaleFactor()));
                playerView.setScaleX(videoZoom[0]);
                playerView.setScaleY(videoZoom[0]);
                showGestureFeedback("Zoom  " + Math.round(videoZoom[0] * 100) + "%");
                return true;
            }
        });
        playerView.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View view, MotionEvent event) {
                zoomGesture.onTouchEvent(event);
                if (playerControlsLocked) return true;
                if (event.getPointerCount() > 1) return true;
                if (event.getAction() == MotionEvent.ACTION_DOWN) {
                    gestureStartX = event.getX();
                    gestureStartY = event.getY();
                    gestureInitialBrightness = getWindow().getAttributes().screenBrightness;
                    if (gestureInitialBrightness < 0) gestureInitialBrightness = .5f;
                    AudioManager audio = (AudioManager) getSystemService(AUDIO_SERVICE);
                    gestureInitialVolume = audio == null ? 0 : audio.getStreamVolume(AudioManager.STREAM_MUSIC);
                    gestureAdjusting = false;
                    gestureSeeking = false;
                    gestureSeekDelta = 0L;
                    return true;
                }
                if (event.getAction() == MotionEvent.ACTION_MOVE) {
                    float deltaX = event.getX() - gestureStartX;
                    float deltaY = event.getY() - gestureStartY;
                    if (!gestureAdjusting && !gestureSeeking && Math.abs(deltaX) > dp(24) && Math.abs(deltaX) > Math.abs(deltaY)) {
                        gestureSeeking = true;
                    }
                    if (gestureSeeking) {
                        gestureSeekDelta = Math.round(deltaX / Math.max(1f, view.getWidth()) * 120000L);
                        long seconds = gestureSeekDelta / 1000L;
                        showGestureFeedback((seconds >= 0 ? "+" : "−") + Math.abs(seconds) + " sec");
                        return true;
                    }
                    if (!gestureAdjusting && Math.abs(deltaY) > dp(22) && Math.abs(deltaY) > Math.abs(event.getX() - gestureStartX)) {
                        gestureAdjusting = true;
                        gestureBrightness = gestureStartX < view.getWidth() / 2f;
                    }
                    if (gestureAdjusting) {
                        float change = -deltaY / Math.max(1f, view.getHeight());
                        if (gestureBrightness) {
                            float value = Math.max(.03f, Math.min(1f, gestureInitialBrightness + change));
                            WindowManager.LayoutParams attributes = getWindow().getAttributes();
                            attributes.screenBrightness = value;
                            getWindow().setAttributes(attributes);
                        } else {
                            AudioManager audio = (AudioManager) getSystemService(AUDIO_SERVICE);
                            if (audio != null) {
                                int maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
                                int value = Math.max(0, Math.min(maximum, gestureInitialVolume + Math.round(change * maximum * 2f)));
                                audio.setStreamVolume(AudioManager.STREAM_MUSIC, value, 0);
                            }
                        }
                        showGestureFeedback(gestureBrightness
                            ? "☼  " + Math.round(getWindow().getAttributes().screenBrightness * 100) + "%"
                            : "◖  " + volumePercent());
                    }
                    return true;
                }
                if (event.getAction() != MotionEvent.ACTION_UP) return true;
                if (gestureSeeking) {
                    if (player != null) player.seekTo(Math.max(0L, player.getCurrentPosition() + gestureSeekDelta));
                    gestureSeeking = false;
                    showPlayerControls();
                    return true;
                }
                if (gestureAdjusting) {
                    gestureAdjusting = false;
                    hideGestureFeedback();
                    return true;
                }
                long now = System.currentTimeMillis();
                if (now - lastPlayerTap < android.view.ViewConfiguration.getDoubleTapTimeout()) {
                    float x = event.getX();
                    float width = Math.max(1, view.getWidth());
                    if (x < width / 3f) {
                        player.seekTo(Math.max(0, player.getCurrentPosition() - 10000L));
                        showGestureFeedback("↶  10 seconds");
                    } else if (x > width * 2f / 3f) {
                        player.seekTo(Math.max(0, player.getCurrentPosition() + 10000L));
                        showGestureFeedback("↷  10 seconds");
                    } else {
                        if (player.isPlaying()) player.pause(); else player.play();
                        showGestureFeedback(player.isPlaying() ? "▶" : "Ⅱ");
                    }
                    showPlayerControls();
                    lastPlayerTap = 0L;
                    return true;
                }
                lastPlayerTap = now;
                main.postDelayed(() -> {
                    if (lastPlayerTap == now) togglePlayerControls();
                }, android.view.ViewConfiguration.getDoubleTapTimeout() + 20L);
                return true;
            }
        });
        buildPlayerControls(stage, episode);
        root.addView(stage, new FrameLayout.LayoutParams(-1, -1));
        playerView.post(() -> updatePictureInPictureParams(isVideoPlaying()));
        startPlayerProgressUpdates();
        schedulePlayerControlsHide();
    }

    private void buildPlayerControls(FrameLayout stage, JSONObject episode) {
        LinearLayout top = row();
        top.setPadding(dp(10), dp(8), dp(10), dp(8));
        top.setBackground(verticalScrim(true));
        Button back = playerIcon("‹", "Back to episodes");
        back.setOnClickListener(v -> { releasePlayer(); showSeriesDetails(); renderEpisodes(false); });
        TextView title = text(episode.optString("title", "Now playing"), 13, TEXT, true);
        title.setMaxLines(1);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        top.addView(back);
        top.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        playerPipButton = playerIcon("▣", "Picture in picture");
        playerPipButton.setContentDescription("Picture in picture while video is playing");
        playerPipButton.setVisibility(isVideoPlaying() ? View.VISIBLE : View.GONE);
        playerPipButton.setOnClickListener(v -> enterPip());
        top.addView(playerPipButton);
        Button full = playerIcon("⛶", "Toggle fullscreen");
        full.setContentDescription("Toggle fullscreen and landscape");
        full.setOnClickListener(v -> toggleFullscreen());
        top.addView(full);
        FrameLayout.LayoutParams topParams = new FrameLayout.LayoutParams(-1, -2, Gravity.TOP);
        topParams.setMargins(dp(10), dp(16), dp(10), 0);
        stage.addView(top, topParams);

        playerLockButton = playerIcon("◉", "Lock player controls");
        playerLockButton.setOnClickListener(v -> {
            playerControlsLocked = !playerControlsLocked;
            playerLockButton.setText(playerControlsLocked ? "⊘" : "◉");
            playerLockButton.setContentDescription(playerControlsLocked ? "Unlock player controls" : "Lock player controls");
            if (playerControlsLocked) hidePlayerControls(); else showPlayerControls();
        });
        FrameLayout.LayoutParams lockParams = new FrameLayout.LayoutParams(-2, -2, Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        lockParams.setMargins(0, 0, dp(12), 0);
        stage.addView(playerLockButton, lockParams);

        LinearLayout center = row();
        center.setGravity(Gravity.CENTER);
        Button backTen = playerIcon("↶", "Rewind 10 seconds");
        backTen.setOnClickListener(v -> seekBy(-10000));
        playerPlayButton = text("Ⅱ", 28, TEXT, true);
        playerPlayButton.setGravity(Gravity.CENTER);
        playerPlayButton.setBackground(rounded(Color.argb(78, 255, 255, 255), 36, Color.argb(100, 255, 255, 255)));
        playerPlayButton.setContentDescription("Play or pause");
        playerPlayButton.setOnClickListener(v -> {
            if (player == null) return;
            if (player.isPlaying()) player.pause(); else player.play();
            showPlayerControls();
        });
        Button forwardTen = playerIcon("↷", "Forward 10 seconds");
        forwardTen.setOnClickListener(v -> seekBy(10000));
        LinearLayout.LayoutParams playParams = new LinearLayout.LayoutParams(dp(64), dp(64));
        playParams.setMargins(dp(24), 0, dp(24), 0);
        center.addView(backTen, new LinearLayout.LayoutParams(dp(58), dp(48)));
        center.addView(playerPlayButton, playParams);
        center.addView(forwardTen, new LinearLayout.LayoutParams(dp(58), dp(48)));
        stage.addView(center, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER));

        LinearLayout bottom = new LinearLayout(this);
        bottom.setOrientation(LinearLayout.VERTICAL);
        bottom.setPadding(dp(14), dp(10), dp(14), dp(12));
        bottom.setBackground(verticalScrim(false));
        LinearLayout seekRow = row();
        playbackTime = text("00:00", 11, TEXT, false);
        playbackDuration = text("00:00", 11, TEXT, false);
        playbackSeek = new SeekBar(this);
        playbackSeek.setMax(1000);
        playbackSeek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                if (fromUser && player != null && player.getDuration() > 0) {
                    long target = player.getDuration() * progress / 1000L;
                    playbackTime.setText(formatPlayerTime(target));
                }
            }
            @Override public void onStartTrackingTouch(SeekBar seekBar) { userSeeking = true; }
            @Override public void onStopTrackingTouch(SeekBar seekBar) {
                if (player != null && player.getDuration() > 0) player.seekTo(player.getDuration() * seekBar.getProgress() / 1000L);
                userSeeking = false;
                schedulePlayerControlsHide();
            }
        });
        seekRow.addView(playbackTime);
        seekRow.addView(playbackSeek, new LinearLayout.LayoutParams(0, dp(38), 1));
        seekRow.addView(playbackDuration);
        bottom.addView(seekRow);
        HorizontalScrollView toolScroller = new HorizontalScrollView(this);
        toolScroller.setHorizontalScrollBarEnabled(false);
        LinearLayout tools = row();
        Button speed = playerIcon(String.format(Locale.ROOT, "%.2g×", playbackSpeed), "Playback speed");
        speed.setOnClickListener(v -> showSpeedMenu(speed));
        Button audio = playerIcon("♫", "Audio track");
        audio.setOnClickListener(v -> showTrackMenu(audio, C.TRACK_TYPE_AUDIO));
        Button subtitles = playerIcon("CC", "Subtitles");
        subtitles.setOnClickListener(v -> showTrackMenu(subtitles, C.TRACK_TYPE_TEXT));
        Button quality = playerIcon("⚙", "Video quality");
        quality.setOnClickListener(v -> showTrackMenu(quality, C.TRACK_TYPE_VIDEO));
        Button fit = playerIcon("▣", "Fit, zoom or stretch");
        fit.setOnClickListener(v -> cycleResizeMode(fit));
        Button next = playerIcon("»", "Next episode");
        next.setOnClickListener(v -> playNextEpisode());
        Button repeat = playerIcon(repeatCurrent ? "↻" : "↻̸", "Repeat episode");
        repeat.setOnClickListener(v -> {
            repeatCurrent = !repeatCurrent;
            if (player != null) player.setRepeatMode(repeatCurrent ? Player.REPEAT_MODE_ONE : Player.REPEAT_MODE_OFF);
            repeat.setText(repeatCurrent ? "↻" : "↻̸");
            showPlayerControls();
        });
        for (Button action : new Button[]{speed, audio, subtitles, quality, fit, repeat, next}) {
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, dp(38));
            params.setMargins(0, dp(4), dp(7), 0);
            tools.addView(action, params);
        }
        toolScroller.addView(tools);
        bottom.addView(toolScroller);
        FrameLayout.LayoutParams bottomParams = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        bottomParams.setMargins(dp(10), 0, dp(10), dp(14));
        stage.addView(bottom, bottomParams);

        playerGestureFeedback = text("", 17, TEXT, true);
        playerGestureFeedback.setGravity(Gravity.CENTER);
        playerGestureFeedback.setPadding(dp(18), dp(12), dp(18), dp(12));
        playerGestureFeedback.setBackground(rounded(Color.argb(145, 8, 10, 15), 28, Color.argb(45, 255, 255, 255)));
        playerGestureFeedback.setVisibility(View.GONE);
        FrameLayout.LayoutParams feedbackParams = new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER);
        stage.addView(playerGestureFeedback, feedbackParams);
    }

    private Button playerIcon(String glyph, String description) {
        Button control = button(glyph, false);
        control.setAllCaps(false);
        control.setMinWidth(dp(42));
        control.setMinimumWidth(dp(42));
        control.setMinHeight(dp(42));
        control.setMinimumHeight(dp(42));
        control.setPadding(dp(8), 0, dp(8), 0);
        control.setTextSize(glyph.length() > 2 ? 13 : 19);
        control.setContentDescription(description);
        control.setBackground(rounded(Color.argb(42, 255, 255, 255), 24, Color.argb(48, 255, 255, 255)));
        return control;
    }

    private String volumePercent() {
        AudioManager audio = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (audio == null || audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC) == 0) return "0%";
        return Math.round(audio.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)) + "%";
    }

    private void showGestureFeedback(String message) {
        if (playerGestureFeedback == null) return;
        playerGestureFeedback.setText(message);
        playerGestureFeedback.setVisibility(View.VISIBLE);
        main.postDelayed(() -> { if (playerGestureFeedback != null) playerGestureFeedback.setVisibility(View.GONE); }, 850L);
    }

    private void hideGestureFeedback() {
        if (playerGestureFeedback != null) playerGestureFeedback.setVisibility(View.GONE);
    }

    private GradientDrawable verticalScrim(boolean fromTop) {
        GradientDrawable drawable = new GradientDrawable(
            fromTop ? GradientDrawable.Orientation.TOP_BOTTOM : GradientDrawable.Orientation.BOTTOM_TOP,
            new int[]{Color.argb(195, 4, 6, 10), Color.argb(95, 4, 6, 10), Color.TRANSPARENT});
        drawable.setCornerRadius(dp(2));
        return drawable;
    }

    private void showSpeedMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        float[] speeds = {.5f, .75f, 1f, 1.25f, 1.5f, 1.75f, 2f};
        for (int i = 0; i < speeds.length; i++) menu.getMenu().add(0, i, i, String.format(Locale.ROOT, "%.2g×", speeds[i]));
        menu.setOnMenuItemClickListener(item -> {
            playbackSpeed = speeds[item.getItemId()];
            if (player != null) player.setPlaybackSpeed(playbackSpeed);
            anchor.setContentDescription("Playback speed " + playbackSpeed + " times");
            ((Button) anchor).setText(String.format(Locale.ROOT, "%.2g×", playbackSpeed));
            showPlayerControls();
            return true;
        });
        menu.show();
    }

    private void showTrackMenu(View anchor, int trackType) {
        if (player == null) return;
        PopupMenu menu = new PopupMenu(this, anchor);
        if (trackType == C.TRACK_TYPE_VIDEO) menu.getMenu().add(0, -1, 0, "Auto quality");
        if (trackType == C.TRACK_TYPE_TEXT) menu.getMenu().add(0, -2, 0, "Subtitles off");
        List<Tracks.Group> groups = player.getCurrentTracks().getGroups();
        int menuId = 1;
        for (Tracks.Group group : groups) {
            if (group.getType() != trackType) continue;
            for (int i = 0; i < group.length; i++) {
                if (!group.isTrackSupported(i)) continue;
                Format format = group.getTrackFormat(i);
                String label;
                if (trackType == C.TRACK_TYPE_VIDEO) {
                    label = format.height > 0 ? format.height + "p" : (format.width > 0 ? format.width + " px" : "Video track");
                    if (format.bitrate > 0) label += " · " + Math.round(format.bitrate / 1000f) + " kbps";
                } else {
                    label = format.label != null ? format.label : format.language != null ? format.language : (trackType == C.TRACK_TYPE_AUDIO ? "Audio track" : "Subtitle track");
                    if (trackType == C.TRACK_TYPE_AUDIO && format.channelCount > 0) label += " · " + format.channelCount + " ch";
                }
                final int selectedId = menuId++;
                final Tracks.Group selectedGroup = group;
                final int trackIndex = i;
                menu.getMenu().add(0, selectedId, selectedId, label + (group.isTrackSelected(i) ? " ✓" : ""));
                menu.getMenu().findItem(selectedId).setOnMenuItemClickListener(item -> {
                    TrackSelectionParameters.Builder builder = player.getTrackSelectionParameters().buildUpon()
                        .clearOverridesOfType(trackType).setTrackTypeDisabled(trackType, false)
                        .setOverrideForType(new TrackSelectionOverride(selectedGroup.getMediaTrackGroup(), trackIndex));
                    player.setTrackSelectionParameters(builder.build());
                    showPlayerControls();
                    return true;
                });
            }
        }
        if (menu.getMenu().size() == 0) menu.getMenu().add("No alternate tracks in this video");
        menu.setOnMenuItemClickListener(item -> {
            if (trackType == C.TRACK_TYPE_VIDEO && item.getItemId() == -1) {
                player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                    .clearOverridesOfType(trackType).setTrackTypeDisabled(trackType, false).build());
                showPlayerControls();
                return true;
            }
            if (trackType == C.TRACK_TYPE_TEXT && item.getItemId() == -2) {
                player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                    .clearOverridesOfType(trackType).setTrackTypeDisabled(trackType, true).build());
                showPlayerControls();
                return true;
            }
            return false;
        });
        menu.show();
    }

    private void cycleResizeMode(Button button) {
        if (playerView == null) return;
        int mode = playerView.getResizeMode();
        int next = mode == AspectRatioFrameLayout.RESIZE_MODE_FIT ? AspectRatioFrameLayout.RESIZE_MODE_ZOOM
            : mode == AspectRatioFrameLayout.RESIZE_MODE_ZOOM ? AspectRatioFrameLayout.RESIZE_MODE_FILL
            : AspectRatioFrameLayout.RESIZE_MODE_FIT;
        playerView.setResizeMode(next);
        String modeName = next == AspectRatioFrameLayout.RESIZE_MODE_FIT ? "Fit" : next == AspectRatioFrameLayout.RESIZE_MODE_ZOOM ? "Zoom" : "Stretch";
        button.setText(next == AspectRatioFrameLayout.RESIZE_MODE_FIT ? "▣" : next == AspectRatioFrameLayout.RESIZE_MODE_ZOOM ? "⌕" : "↔");
        button.setContentDescription("Video sizing: " + modeName);
        showPlayerControls();
    }

    private void toggleFullscreen() {
        fullscreen = !fullscreen;
        if (fullscreen) {
            setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
            if (Build.VERSION.SDK_INT >= 30 && getWindow().getInsetsController() != null) {
                getWindow().getInsetsController().hide(android.view.WindowInsets.Type.systemBars());
                getWindow().getInsetsController().setSystemBarsBehavior(android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            } else getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        } else {
            setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
            if (Build.VERSION.SDK_INT >= 30 && getWindow().getInsetsController() != null) getWindow().getInsetsController().show(android.view.WindowInsets.Type.systemBars());
            else getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        }
        showPlayerControls();
    }

    private void seekBy(long delta) {
        if (player != null) player.seekTo(Math.max(0, player.getCurrentPosition() + delta));
        showPlayerControls();
    }

    private void playNextEpisode() {
        if (playingEpisode == null || currentSeriesId.isEmpty()) return;
        int currentNumber = playingEpisode.optInt("number", -1);
        JSONObject nextEpisode = null;
        for (int i = 0; i < episodes.length(); i++) {
            JSONObject candidate = episodes.optJSONObject(i);
            if (candidate == null) continue;
            int number = candidate.optInt("number", -1);
            if (number > currentNumber && (nextEpisode == null || number < nextEpisode.optInt("number", Integer.MAX_VALUE))) nextEpisode = candidate;
        }
        if (nextEpisode != null) playEpisode(nextEpisode);
        else showToast("You’re at the latest playable episode.");
    }

    private void startPlayerProgressUpdates() {
        if (playerProgressUpdate != null) main.removeCallbacks(playerProgressUpdate);
        playerProgressUpdate = new Runnable() {
            @Override public void run() {
                if (player == null || playbackSeek == null) return;
                long duration = player.getDuration();
                long position = player.getCurrentPosition();
                if (System.currentTimeMillis() - lastPlaybackSaveAt > 2000) {
                    savePlaybackPosition();
                    lastPlaybackSaveAt = System.currentTimeMillis();
                }
                if (duration > 0) {
                    if (!userSeeking) playbackSeek.setProgress((int) Math.min(1000, position * 1000L / duration));
                    playbackTime.setText(formatPlayerTime(position));
                    playbackDuration.setText(formatPlayerTime(duration));
                } else {
                    playbackTime.setText(formatPlayerTime(position));
                    playbackDuration.setText("LIVE");
                }
                main.postDelayed(this, 500);
            }
        };
        main.post(playerProgressUpdate);
    }

    private String formatPlayerTime(long millis) {
        long seconds = Math.max(0, millis / 1000);
        long hours = seconds / 3600;
        long minutes = (seconds % 3600) / 60;
        long remaining = seconds % 60;
        return hours > 0 ? String.format(Locale.ROOT, "%d:%02d:%02d", hours, minutes, remaining)
            : String.format(Locale.ROOT, "%02d:%02d", minutes, remaining);
    }

    private String playbackPreferenceKey(JSONObject episode) {
        return "position:" + currentSeriesId + ":" + episode.optString("id", "");
    }

    private long savedPlaybackPosition(JSONObject episode) {
        return getSharedPreferences("cinewave-playback", MODE_PRIVATE).getLong(playbackPreferenceKey(episode), 0L);
    }

    private void savePlaybackPosition() {
        if (player == null || playingEpisode == null) return;
        String key = playbackPreferenceKey(playingEpisode);
        long position = player.getCurrentPosition();
        long duration = player.getDuration();
        android.content.SharedPreferences.Editor editor = getSharedPreferences("cinewave-playback", MODE_PRIVATE).edit();
        if (duration > 0 && position >= duration - 3000) editor.remove(key);
        else if (position > 5000) editor.putLong(key, position);
        else editor.remove(key);
        editor.apply();
    }

    private void clearSavedPlaybackPosition() {
        if (playingEpisode == null) return;
        getSharedPreferences("cinewave-playback", MODE_PRIVATE).edit()
            .remove(playbackPreferenceKey(playingEpisode)).apply();
    }

    @Override protected void onPause() {
        savePlaybackPosition();
        super.onPause();
    }

    private void togglePlayerControls() {
        if (!playerControlsVisible) showPlayerControls(); else hidePlayerControls();
    }

    private void showPlayerControls() {
        playerControlsVisible = true;
        if (playerStage != null) for (int i = 1; i < playerStage.getChildCount(); i++) playerStage.getChildAt(i).setVisibility(View.VISIBLE);
        schedulePlayerControlsHide();
    }

    private void hidePlayerControls() {
        playerControlsVisible = false;
        if (playerStage != null) for (int i = 1; i < playerStage.getChildCount(); i++) {
            View child = playerStage.getChildAt(i);
            child.setVisibility(playerControlsLocked && child == playerLockButton ? View.VISIBLE : View.GONE);
        }
    }

    private void schedulePlayerControlsHide() {
        if (playerHideControls != null) main.removeCallbacks(playerHideControls);
        playerHideControls = this::hidePlayerControls;
        main.postDelayed(playerHideControls, 5500);
    }

    private void startDownload(JSONObject episode, Button button) {
        button.setEnabled(false);
        String id = episode.optString("id", "");
        String number = episode.optString("number", "");
        String title = episode.optString("title", "Episode");
        String path = "api/download?series=" + enc(currentSeriesId) + "&episode=" + enc(id) + "&number=" + enc(number) + "&title=" + enc(title);
        api(path, result -> {
            JSONObject job = result.optJSONObject("job");
            if (job == null) { button.setEnabled(true); showToast("The download could not be queued."); return; }
            String jobId = job.optString("id", "");
            TextView progress = text("Preparing download…", 12, MUTED, false);
            ((ViewGroup) button.getParent().getParent()).addView(progress);
            pollDownload(jobId, title, button, progress);
        }, error -> { button.setEnabled(true); showToast("Download unavailable: " + error); });
    }

    private void pollDownload(String jobId, String title, Button button, TextView progress) {
        if (downloadPoll != null) main.removeCallbacks(downloadPoll);
        downloadPoll = () -> api("api/download/" + enc(jobId), result -> {
            JSONObject job = result.optJSONObject("job");
            if (job == null) { progress.setText("Download status is unavailable."); button.setEnabled(true); return; }
            String state = job.optString("state", "queued");
            if ("completed".equals(state)) {
                progress.setText("Download ready · "+android.text.format.Formatter.formatShortFileSize(this, job.optLong("size", 0)));
                button.setEnabled(true);
                exportDownload(jobId, title);
            } else if ("error".equals(state)) {
                progress.setText("Download failed: " + job.optString("error", "Unknown error"));
                button.setEnabled(true);
            } else {
                progress.setText(("downloading".equals(state) ? "Downloading" : "Preparing") + " · " + job.optInt("percent", 0) + "%");
                main.postDelayed(downloadPoll, 1400);
            }
        }, error -> {
            progress.setText("Checking download…");
            main.postDelayed(downloadPoll, 2500);
        });
        downloadPoll.run();
    }

    private void exportDownload(String jobId, String title) {
        String url = APP_URL + "api/download/file/" + enc(jobId);
        if (Build.VERSION.SDK_INT <= 28 && checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            pendingExportUrl = url;
            pendingExportTitle = title;
            requestPermissions(new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 4102);
            return;
        }
        enqueueExport(url, title);
    }

    private void enqueueExport(String url, String title) {
        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(title);
            request.setDescription("CineWave episode download");
            request.setMimeType("video/mp4");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safeName(title) + ".mp4");
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager == null) throw new IllegalStateException("Android download service is unavailable.");
            manager.enqueue(request);
            showToast("Saving to Downloads · check the notification shade.");
        } catch (Exception error) {
            showToast("Download is ready in CineWave, but couldn’t export it: " + error.getMessage());
        }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 4102) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) enqueueExport(pendingExportUrl, pendingExportTitle);
        else showToast("The file is ready in CineWave. Allow storage access to save it to Downloads.");
        pendingExportUrl = "";
        pendingExportTitle = "";
    }

    private void installBaseScreen(String title, boolean withScroll) {
        root.removeAllViews();
        screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setPadding(dp(16), dp(10), dp(16), dp(8));
        root.addView(screen, new FrameLayout.LayoutParams(-1, -1));
        if (withScroll) {
            ScrollView scroll = new ScrollView(this);
            scroll.setFillViewport(true);
            screen.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
            content = new LinearLayout(this);
            content.setOrientation(LinearLayout.VERTICAL);
            content.setPadding(0, 0, 0, dp(18));
            scroll.addView(content, new ScrollView.LayoutParams(-1, -2));
        } else {
            content = screen;
        }
    }

    private void api(String path, ApiSuccess success, ApiFailure failure) {
        apiExecutor.execute(() -> {
            try {
                JSONObject result = requestJson(path);
                main.post(() -> success.accept(result));
            } catch (Exception error) {
                String message = error.getMessage() == null ? "Connection error" : error.getMessage();
                main.post(() -> failure.accept(message));
            }
        });
    }

    private JSONObject requestJson(String path) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(APP_URL + path).openConnection();
        connection.setConnectTimeout(12000);
        connection.setReadTimeout(45000);
        connection.setRequestProperty("Accept", "application/json");
        try {
            int status = connection.getResponseCode();
            InputStream input = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            if (input == null) throw new IOException("Local media service returned HTTP " + status);
            byte[] bytes;
            try (InputStream response = input) { bytes = readAll(response); }
            JSONObject json;
            try { json = new JSONObject(new String(bytes, StandardCharsets.UTF_8)); }
            catch (Exception invalidJson) { throw new IOException("Local media service returned HTTP " + status); }
            if (status >= 400 || !json.optBoolean("ok", true)) throw new IOException(json.optString("error", "Request failed"));
            return json;
        } finally { connection.disconnect(); }
    }

    private byte[] readAll(InputStream input) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[16 * 1024];
        int count;
        while ((count = input.read(buffer)) != -1) out.write(buffer, 0, count);
        return out.toByteArray();
    }

    private void loadImage(String source, ImageView view) {
        if (source == null || source.trim().isEmpty()) return;
        String tag = source.trim();
        view.setTag(tag);
        Bitmap cached = imageCache.get(tag);
        if (cached != null) { view.setImageBitmap(cached); return; }
        imageExecutor.execute(() -> {
            HttpURLConnection connection = null;
            try {
                String url = APP_URL + "api/image?url=" + enc(tag);
                connection = (HttpURLConnection) new URL(url).openConnection();
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(20000);
                connection.setRequestProperty("User-Agent", "CineWave Android");
                try (InputStream input = connection.getInputStream()) {
                    Bitmap decoded = BitmapFactory.decodeStream(input);
                    if (decoded != null) {
                        int width = Math.min(decoded.getWidth(), 520);
                        int height = Math.max(1, Math.round(decoded.getHeight() * (width / (float) decoded.getWidth())));
                        Bitmap bitmap = width == decoded.getWidth() ? decoded : Bitmap.createScaledBitmap(decoded, width, height, true);
                        if (bitmap != decoded) decoded.recycle();
                        imageCache.put(tag, bitmap);
                        main.post(() -> { if (tag.equals(view.getTag())) view.setImageBitmap(bitmap); });
                    }
                }
            } catch (Exception ignored) { }
            finally { if (connection != null) connection.disconnect(); }
        });
    }

    private void copyAssetTree(String assetPath, File destination) throws IOException {
        String[] children = getAssets().list(assetPath);
        if (children == null || children.length == 0) {
            File parent = destination.getParentFile();
            if (parent != null) parent.mkdirs();
            try (InputStream input = getAssets().open(assetPath); FileOutputStream output = new FileOutputStream(destination)) {
                byte[] buffer = new byte[8192]; int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
            return;
        }
        if (!destination.exists() && !destination.mkdirs()) throw new IOException("Cannot create " + destination);
        for (String child : children) copyAssetTree(assetPath + "/" + child, new File(destination, child));
    }

    private boolean isVideoPlaying() {
        if (player == null || playerView == null || !player.isPlaying() || player.getPlaybackState() != Player.STATE_READY) return false;
        androidx.media3.common.VideoSize size = player.getVideoSize();
        return size.width > 0 && size.height > 0;
    }

    private Rect pictureInPictureVideoRect() {
        if (playerView == null || playerView.getWidth() <= 0 || playerView.getHeight() <= 0) return null;
        int[] location = new int[2];
        playerView.getLocationInWindow(location);
        int left = location[0], top = location[1], width = playerView.getWidth(), height = playerView.getHeight();
        int resizeMode = playerView.getResizeMode();
        if (resizeMode == AspectRatioFrameLayout.RESIZE_MODE_FIT && player != null) {
            androidx.media3.common.VideoSize size = player.getVideoSize();
            if (size.width > 0 && size.height > 0) {
                float videoAspect = size.width * size.pixelWidthHeightRatio / size.height;
                float viewAspect = width / (float) height;
                if (viewAspect > videoAspect) {
                    int fittedWidth = Math.round(height * videoAspect);
                    left += (width - fittedWidth) / 2;
                    width = fittedWidth;
                } else {
                    int fittedHeight = Math.round(width / videoAspect);
                    top += (height - fittedHeight) / 2;
                    height = fittedHeight;
                }
            }
        }
        return width > 0 && height > 0 ? new Rect(left, top, left + width, top + height) : null;
    }

    private void enterPip() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) { showToast("Picture-in-picture needs Android 8.0 or newer."); return; }
        if (!isVideoPlaying()) { showToast("Start video playback before opening picture-in-picture."); return; }
        try {
            PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder();
            Rect sourceRect = pictureInPictureVideoRect();
            if (sourceRect != null) builder.setSourceRectHint(sourceRect);
            builder.setAspectRatio(pipAspectRatio());
            PictureInPictureParams params = builder.build();
            enterPictureInPictureMode(params);
        } catch (Exception error) { showToast("Picture-in-picture is unavailable on this device."); }
    }

    private Rational pipAspectRatio() {
        int width = 16, height = 9;
        if (player != null) {
            androidx.media3.common.VideoSize size = player.getVideoSize();
            if (size.width > 0 && size.height > 0) {
                width = size.width;
                height = Math.max(1, Math.round(size.height / size.pixelWidthHeightRatio));
            }
        }
        float ratio = width / (float) height;
        if (ratio < 1f / 2.39f) return new Rational(100, 239);
        if (ratio > 2.39f) return new Rational(239, 100);
        return new Rational(width, height);
    }

    private void updatePictureInPictureParams(boolean playing) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        try {
            PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder().setAspectRatio(pipAspectRatio());
            Rect sourceRect = pictureInPictureVideoRect();
            if (sourceRect != null) builder.setSourceRectHint(sourceRect);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) builder.setAutoEnterEnabled(playing && isVideoPlaying());
            setPictureInPictureParams(builder.build());
        } catch (Exception ignored) { }
    }

    @Override public void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && Build.VERSION.SDK_INT < Build.VERSION_CODES.S && isVideoPlaying()) enterPip();
    }

    @Override public void onPictureInPictureModeChanged(boolean inPip, Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        if (playerView != null) playerView.setUseController(false);
        if (inPip) hidePlayerControls(); else if (player != null) showPlayerControls();
    }

    @Override public void onBackPressed() {
        if (player != null) { releasePlayer(); showSeriesDetails(); renderEpisodes(false); }
        else if (currentSeries != null) showHome();
        else super.onBackPressed();
    }

    private void releasePlayer() {
        savePlaybackPosition();
        if (playerProgressUpdate != null) main.removeCallbacks(playerProgressUpdate);
        if (playerHideControls != null) main.removeCallbacks(playerHideControls);
        if (player != null) { player.release(); player = null; }
        playerPipButton = null;
        updatePictureInPictureParams(false);
        playerView = null;
        playerStage = null;
        playbackSeek = null;
        playbackTime = null;
        playbackDuration = null;
        playerPlayButton = null;
        playingEpisode = null;
        if (fullscreen) {
            fullscreen = false;
            setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
            if (Build.VERSION.SDK_INT >= 30 && getWindow().getInsetsController() != null) getWindow().getInsetsController().show(android.view.WindowInsets.Type.systemBars());
            else getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        }
        WindowManager.LayoutParams windowAttributes = getWindow().getAttributes();
        windowAttributes.screenBrightness = -1f;
        getWindow().setAttributes(windowAttributes);
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private boolean matchesCategory(JSONObject item, String category) {
        if ("All".equals(category)) return true;
        String type = item.optString("content_type", "Series");
        String all = (genres(item) + " " + type).toLowerCase(Locale.ROOT);
        if ("Telefilms".equals(category)) return "Telefilm".equalsIgnoreCase(type) || all.contains("telefilm");
        if ("Shows".equals(category)) return "Show".equalsIgnoreCase(type) || all.contains("tv show") || all.contains("reality") || all.contains("game show");
        if ("Movies".equals(category)) return "Movie".equalsIgnoreCase(type);
        if ("Series".equals(category)) return "Series".equalsIgnoreCase(type);
        return all.contains(category.toLowerCase(Locale.ROOT));
    }

    private String genres(JSONObject item) {
        JSONArray values = item.optJSONArray("genres");
        if (values == null) values = item.optJSONArray("catalogue_genres");
        if (values == null) return item.optString("genres", "");
        List<String> out = new ArrayList<>();
        for (int i = 0; i < values.length(); i++) if (!values.optString(i).isEmpty()) out.add(values.optString(i));
        return android.text.TextUtils.join(" · ", out);
    }

    private String posterOf(JSONObject item) {
        String[] keys = {"poster", "poster_url", "cover_image", "cover", "image", "image_url", "thumbnail", "thumbnail_url", "backdrop"};
        for (String key : keys) {
            String value = item.optString(key, "");
            if (!value.isEmpty() && !"null".equals(value)) return value;
        }
        return "";
    }

    private LinearLayout row() { LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.HORIZONTAL); row.setGravity(Gravity.CENTER_VERTICAL); return row; }
    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value == null ? "" : value);
        view.setTextSize(size);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }
    private Button button(String label, boolean primary) {
        Button button = new Button(this);
        button.setText(label);
        button.setTransformationMethod(null);
        button.setTextSize(13);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setTextColor(primary ? Color.WHITE : TEXT);
        button.setPadding(dp(14), 0, dp(14), 0);
        button.setMinHeight(dp(42));
        button.setBackground(rounded(primary ? ACCENT : SURFACE_2, 13, primary ? 0 : Color.rgb(50, 57, 72)));
        return button;
    }
    private GradientDrawable rounded(int color, int radius, int stroke) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(radius));
        if (stroke != 0) shape.setStroke(dp(1), stroke);
        return shape;
    }
    private String enc(String value) {
        try { return URLEncoder.encode(value == null ? "" : value, "UTF-8"); }
        catch (Exception ignored) { return ""; }
    }
    private String safeName(String value) {
        String safe = value == null ? "episode" : value.replaceAll("[^A-Za-z0-9._ -]", "-").trim();
        while (safe.endsWith(".")) safe = safe.substring(0, safe.length() - 1);
        return safe.isEmpty() ? "cinewave-episode" : safe;
    }
    private int dp(float value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void showToast(String message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }

    @Override protected void onDestroy() {
        if (downloadPoll != null) main.removeCallbacks(downloadPoll);
        if (searchUpdate != null) main.removeCallbacks(searchUpdate);
        if (cataloguePoll != null) main.removeCallbacks(cataloguePoll);
        if (episodePoll != null) main.removeCallbacks(episodePoll);
        if (episodeRefreshCheck != null) main.removeCallbacks(episodeRefreshCheck);
        if (playerProgressUpdate != null) main.removeCallbacks(playerProgressUpdate);
        releasePlayer();
        backendExecutor.shutdownNow();
        apiExecutor.shutdownNow();
        imageExecutor.shutdownNow();
        super.onDestroy();
    }

    private interface ApiSuccess { void accept(JSONObject result); }
    private interface ApiFailure { void accept(String error); }
}
