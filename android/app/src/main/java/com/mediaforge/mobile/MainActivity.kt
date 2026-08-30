package com.mediaforge.mobile

import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.mediaforge.mobile.databinding.ActivityMainBinding
import com.mediaforge.mobile.databinding.ItemMediaBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** One row in the queue. */
data class MediaItem(
    val url: String,
    var title: String,
    val duration: Int,
    val collection: String,
    val index: Int,
    val total: Int,
) {
    var status: String = "QUEUED"
    var percent: Int = 0
    var detail: String = ""
    var outputPath: String = ""
}

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var adapter: QueueAdapter

    private val items = mutableListOf<MediaItem>()
    private val cancelled = AtomicBoolean(false)
    private var running = false

    private val qualities = listOf("MAX", "1080p", "720p", "480p", "360p", "AUDIO")

    /** Chaquopy calls these from Python while a download is in flight. */
    inner class Progress(private val position: Int) {
        fun isCancelled(): Boolean = cancelled.get()

        fun onProgress(json: String) {
            val o = runCatching { JSONObject(json) }.getOrNull() ?: return
            val percent = o.optDouble("percent", 0.0).toInt()
            val speed = o.optDouble("speed", 0.0)
            val stage = o.optString("stage", "")
            runOnUiThread {
                items.getOrNull(position)?.let {
                    it.percent = percent
                    it.status = if (stage == "audio") "AUDIO" else "FETCHING"
                    it.detail = "%d%%  %s".format(percent, humanSpeed(speed))
                    adapter.notifyItemChanged(position)
                    updateOverall()
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        if (!Python.isStarted()) Python.start(AndroidPlatform(this))

        adapter = QueueAdapter(items)
        binding.queueList.layoutManager = LinearLayoutManager(this)
        binding.queueList.adapter = adapter

        binding.qualitySpinner.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item, qualities
        )
        binding.qualitySpinner.setSelection(1)

        binding.analyzeButton.setOnClickListener { analyze() }
        binding.clearButton.setOnClickListener {
            binding.urlInput.setText("")
            items.clear()
            adapter.notifyDataSetChanged()
            updateOverall()
        }
        binding.pasteButton.setOnClickListener { paste() }
        binding.executeButton.setOnClickListener { execute() }
        binding.abortButton.setOnClickListener {
            cancelled.set(true)
            log("abort requested", R.color.amber)
        }

        lifecycleScope.launch {
            val version = withContext(Dispatchers.IO) {
                runCatching { engine().callAttr("engine_version").toString() }.getOrDefault("?")
            }
            binding.engineLabel.text = "core $version  ·  no ffmpeg needed"
        }

        handleShare(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** A link shared from YouTube or Instagram lands here. */
    private fun handleShare(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            intent.getStringExtra(Intent.EXTRA_TEXT)?.let {
                binding.urlInput.setText(it.trim())
                analyze()
            }
        }
    }

    private fun engine(): PyObject = Python.getInstance().getModule("mf_engine")

    private fun paste() {
        val clip = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clip.primaryClip?.getItemAt(0)?.text?.let { binding.urlInput.setText(it.toString().trim()) }
    }

    private fun analyze() {
        val raw = binding.urlInput.text.toString().trim()
        if (raw.isEmpty()) {
            toast("Paste a link first")
            return
        }
        setBusy(true, "ANALYZING")
        log("resolving $raw")

        lifecycleScope.launch {
            val json = withContext(Dispatchers.IO) {
                engine().callAttr("probe", raw, null).toString()
            }
            val o = JSONObject(json)
            if (!o.optBoolean("ok")) {
                log("could not read link: ${o.optString("error")}", R.color.red)
                setBusy(false, "IDLE")
                return@launch
            }
            val arr = o.getJSONArray("items")
            var added = 0
            for (i in 0 until arr.length()) {
                val e = arr.getJSONObject(i)
                val url = e.getString("url")
                if (items.any { it.url == url }) continue
                items.add(
                    MediaItem(
                        url = url,
                        title = e.optString("title", url),
                        duration = e.optInt("duration", 0),
                        collection = e.optString("collection", ""),
                        index = e.optInt("index", 1),
                        total = e.optInt("total", 1),
                    )
                )
                added++
            }
            adapter.notifyDataSetChanged()
            binding.urlInput.setText("")
            log(if (arr.length() > 1) "collection: ${arr.length()} media, $added queued"
                else "queued ${o.optString("title")}", R.color.green)
            setBusy(false, "IDLE")
            updateOverall()
        }
    }

    private fun execute() {
        val pending = items.withIndex().filter { it.value.status == "QUEUED" }
        if (pending.isEmpty()) {
            toast("Nothing queued")
            return
        }
        cancelled.set(false)
        setBusy(true, "RUNNING")
        val quality = qualities[binding.qualitySpinner.selectedItemPosition]
        val audioLang = binding.audioLangInput.text.toString().trim()
        val outDir = File(getExternalFilesDir(Environment.DIRECTORY_MOVIES), "MediaForge")

        lifecycleScope.launch {
            for ((position, item) in pending) {
                if (cancelled.get()) {
                    item.status = "ABORTED"
                    adapter.notifyItemChanged(position)
                    continue
                }
                item.status = "FETCHING"
                adapter.notifyItemChanged(position)

                val result = withContext(Dispatchers.IO) {
                    runCatching {
                        engine().callAttr(
                            "download", item.url, outDir.absolutePath, quality,
                            audioLang, null, Progress(position)
                        ).toString()
                    }.getOrElse { """{"ok":false,"error":"${it.message}"}""" }
                }
                finish(position, item, JSONObject(result))
            }
            setBusy(false, "IDLE")
            val done = items.count { it.status == "DONE" }
            val failed = items.count { it.status == "FAILED" }
            log("batch finished · $done ok · $failed failed",
                if (failed == 0) R.color.green else R.color.amber)
        }
    }

    private suspend fun finish(position: Int, item: MediaItem, o: JSONObject) {
        if (!o.optBoolean("ok")) {
            item.status = if (o.optBoolean("cancelled")) "ABORTED" else "FAILED"
            item.detail = o.optString("error").take(80)
            adapter.notifyItemChanged(position)
            if (item.status == "FAILED") log("failed: ${item.detail}", R.color.red)
            return
        }

        var path = o.optString("path")
        if (o.optBoolean("needs_mux")) {
            item.status = "MUXING"
            item.detail = "combining video + audio"
            adapter.notifyItemChanged(position)
            val muxed = withContext(Dispatchers.IO) {
                runCatching {
                    val out = Muxer.mux(o.getString("video"), o.getString("audio"), o.getString("out"))
                    File(o.getString("video")).delete()
                    File(o.getString("audio")).delete()
                    out
                }
            }
            if (muxed.isFailure) {
                item.status = "FAILED"
                item.detail = "mux failed: ${muxed.exceptionOrNull()?.message}".take(80)
                adapter.notifyItemChanged(position)
                return
            }
            path = muxed.getOrThrow()
        }

        val exported = withContext(Dispatchers.IO) { exportToDownloads(File(path)) }
        item.status = "DONE"
        item.percent = 100
        item.outputPath = exported ?: path
        item.detail = File(path).name
        adapter.notifyItemChanged(position)
        updateOverall()
        log("saved ${File(path).name}", R.color.green)
    }

    /** Copy the finished file into the shared Downloads folder so it is visible. */
    private fun exportToDownloads(file: File): String? {
        if (!file.exists()) return null
        return runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, file.name)
                    put(MediaStore.Downloads.MIME_TYPE,
                        if (file.extension == "m4a") "audio/mp4" else "video/mp4")
                    put(MediaStore.Downloads.RELATIVE_PATH,
                        Environment.DIRECTORY_DOWNLOADS + "/MediaForge")
                }
                val uri = contentResolver.insert(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI, values
                ) ?: return null
                contentResolver.openOutputStream(uri)?.use { out ->
                    file.inputStream().use { it.copyTo(out) }
                }
                file.delete()
                "Downloads/MediaForge/${file.name}"
            } else {
                file.absolutePath
            }
        }.getOrNull()
    }

    private fun setBusy(busy: Boolean, state: String) {
        running = busy
        binding.executeButton.isEnabled = !busy
        binding.analyzeButton.isEnabled = !busy
        binding.abortButton.isEnabled = busy
        binding.statusLabel.text = state
        val colour = when (state) {
            "RUNNING" -> R.color.accent
            "ANALYZING" -> R.color.blue
            else -> R.color.text_dim
        }
        binding.statusDot.setBackgroundColor(ContextCompat.getColor(this, colour))
        binding.statusLabel.setTextColor(ContextCompat.getColor(this, colour))
    }

    private fun updateOverall() {
        if (items.isEmpty()) {
            binding.overallProgress.progress = 0
            return
        }
        binding.overallProgress.progress =
            items.sumOf { if (it.status == "DONE") 100 else it.percent } / items.size
    }

    private fun log(message: String, colour: Int = R.color.text_dim) {
        binding.logLine.text = message
        binding.logLine.setTextColor(ContextCompat.getColor(this, colour))
    }

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private fun humanSpeed(bytesPerSecond: Double): String {
        if (bytesPerSecond <= 0) return ""
        val units = listOf("B", "KB", "MB", "GB")
        var value = bytesPerSecond
        var unit = 0
        while (value >= 1024 && unit < units.lastIndex) {
            value /= 1024; unit++
        }
        return "%.1f %s/s".format(value, units[unit])
    }

    /** Queue list. */
    inner class QueueAdapter(private val data: List<MediaItem>) :
        RecyclerView.Adapter<QueueAdapter.Holder>() {

        inner class Holder(val v: ItemMediaBinding) : RecyclerView.ViewHolder(v.root)

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(ItemMediaBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount() = data.size

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val item = data[position]
            holder.v.itemTitle.text = item.title
            val where = if (item.collection.isNotEmpty())
                "${item.collection}  [${item.index}/${item.total}]" else item.url
            holder.v.itemMeta.text = where
            holder.v.itemProgress.progress = item.percent
            holder.v.itemStatus.text =
                if (item.detail.isEmpty()) item.status else "${item.status}  ·  ${item.detail}"
            val colour = when (item.status) {
                "DONE" -> R.color.green
                "FAILED" -> R.color.red
                "ABORTED" -> R.color.amber
                "QUEUED" -> R.color.text_dim
                else -> R.color.accent
            }
            holder.v.itemStatus.setTextColor(ContextCompat.getColor(this@MainActivity, colour))
            holder.v.root.setOnClickListener {
                if (item.outputPath.isNotEmpty()) toast(item.outputPath)
            }
        }
    }
}
