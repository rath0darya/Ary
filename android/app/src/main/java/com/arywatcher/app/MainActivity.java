package com.arywatcher.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.app.PictureInPictureParams;
import android.content.Context;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
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
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.ui.PlayerView;

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
        showLoading("Loading your collection", "Finding available titles…");
        api("api/series" + (refresh ? "?refresh=1" : ""), result -> {
            allSeries = result.optJSONArray("series");
            if (allSeries == null) allSeries = new JSONArray();
            showHome();
        }, error -> showError("Couldn’t load the catalogue", error, () -> loadCatalogue(refresh)));
    }

    private void showHome() {
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
        if (status != null) status.setText(matches.size() + (matches.size() == 1 ? " title" : " titles"));
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
        Button refresh = button("Refresh", false);
        refresh.setOnClickListener(v -> loadEpisodes(true));
        episodeHead.addView(refresh);
        LinearLayout.LayoutParams episodeHeadParams = new LinearLayout.LayoutParams(-1, -2);
        episodeHeadParams.setMargins(0, dp(18), 0, dp(8));
        detail.addView(episodeHead, episodeHeadParams);
        episodeList = new LinearLayout(this);
        episodeList.setOrientation(LinearLayout.VERTICAL);
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
            renderEpisodes(refresh);
        }, error -> showToast("Episodes unavailable: " + error));
    }

    private void renderEpisodes(boolean refresh) {
        if (episodeList == null) return;
        episodeList.removeAllViews();
        if (refresh) showToast("Episode catalogue refreshed");
        if (episodes.length() == 0) {
            TextView empty = text("No episodes are listed for this title yet.", 14, MUTED, false);
            episodeList.addView(empty);
            return;
        }
        for (int i = 0; i < episodes.length(); i++) {
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
        return card;
    }

    private void playEpisode(JSONObject episode) {
        if (currentSeriesId.isEmpty()) return;
        String id = episode.optString("id", "");
        if (id.isEmpty()) { showToast("This video has no episode ID."); return; }
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

    private void startNativePlayer(String source, JSONObject episode) {
        releasePlayer();
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
        playerView = new PlayerView(this);
        playerView.setPlayer(player);
        playerView.setUseController(true);
        playerView.setShowBuffering(PlayerView.SHOW_BUFFERING_ALWAYS);
        playerView.setBackgroundColor(Color.BLACK);
        playerView.setContentDescription("Video player");
        player.addListener(new Player.Listener() {
            @Override public void onPlayerError(PlaybackException error) {
                showToast("Playback failed: " + error.getMessage());
            }
            @Override public void onIsPlayingChanged(boolean isPlaying) {
                updatePictureInPictureParams(isPlaying);
            }
        });
        player.setMediaItem(MediaItem.fromUri(Uri.parse(source)));
        player.prepare();
        player.play();
        root.removeAllViews();
        FrameLayout stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.BLACK);
        stage.addView(playerView, new FrameLayout.LayoutParams(-1, -1));
        Button back = button("‹  Episodes", false);
        FrameLayout.LayoutParams backParams = new FrameLayout.LayoutParams(-2, dp(44), Gravity.TOP | Gravity.START);
        backParams.setMargins(dp(12), dp(12), 0, 0);
        stage.addView(back, backParams);
        back.setOnClickListener(v -> { releasePlayer(); showSeriesDetails(); renderEpisodes(false); });
        Button pip = button("▣", false);
        pip.setContentDescription("Picture in picture");
        FrameLayout.LayoutParams pipParams = new FrameLayout.LayoutParams(dp(48), dp(44), Gravity.TOP | Gravity.END);
        pipParams.setMargins(0, dp(12), dp(12), 0);
        stage.addView(pip, pipParams);
        pip.setOnClickListener(v -> enterPip());
        root.addView(stage, new FrameLayout.LayoutParams(-1, -1));
        TextView episodeTitle = text(episode.optString("title", "Now playing"), 13, TEXT, true);
        episodeTitle.setMaxLines(1);
        episodeTitle.setEllipsize(android.text.TextUtils.TruncateAt.END);
        FrameLayout.LayoutParams titleParams = new FrameLayout.LayoutParams(-1, dp(42), Gravity.BOTTOM);
        titleParams.setMargins(dp(16), 0, dp(16), dp(10));
        stage.addView(episodeTitle, titleParams);
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
        try (InputStream input = connection.getInputStream()) {
            byte[] bytes = readAll(input);
            JSONObject json = new JSONObject(new String(bytes, StandardCharsets.UTF_8));
            if (connection.getResponseCode() >= 400 || !json.optBoolean("ok", true)) {
                throw new IOException(json.optString("error", "Request failed"));
            }
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

    private void enterPip() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || player == null) { showToast("Picture-in-picture needs Android 8.0 or newer."); return; }
        try {
            PictureInPictureParams params = new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9)).build();
            enterPictureInPictureMode(params);
        } catch (Exception error) { showToast("Picture-in-picture is unavailable on this device."); }
    }

    private void updatePictureInPictureParams(boolean playing) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        try {
            PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9));
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) builder.setAutoEnterEnabled(playing);
            setPictureInPictureParams(builder.build());
        } catch (Exception ignored) { }
    }

    @Override public void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && Build.VERSION.SDK_INT < Build.VERSION_CODES.S && player != null && player.isPlaying()) enterPip();
    }

    @Override public void onPictureInPictureModeChanged(boolean inPip, Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        if (playerView != null) playerView.setUseController(!inPip);
    }

    @Override public void onBackPressed() {
        if (player != null) { releasePlayer(); showSeriesDetails(); renderEpisodes(false); }
        else if (currentSeries != null) showHome();
        else super.onBackPressed();
    }

    private void releasePlayer() {
        if (player != null) { player.release(); player = null; }
        playerView = null;
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
        releasePlayer();
        backendExecutor.shutdownNow();
        apiExecutor.shutdownNow();
        imageExecutor.shutdownNow();
        super.onDestroy();
    }

    private interface ApiSuccess { void accept(JSONObject result); }
    private interface ApiFailure { void accept(String error); }
}
