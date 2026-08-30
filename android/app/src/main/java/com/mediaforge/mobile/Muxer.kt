package com.mediaforge.mobile

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/**
 * Combines a video-only and an audio-only file into one MP4.
 *
 * Android has no ffmpeg, but MediaMuxer can write an MP4 holding an H.264
 * video track and an AAC audio track by copying the encoded samples across
 * untouched - so this is a remux, not a re-encode: no quality is lost and it
 * takes a second or two rather than minutes.
 */
object Muxer {

    private const val BUFFER_SIZE = 1 shl 20

    class MuxException(message: String) : Exception(message)

    fun mux(videoPath: String, audioPath: String, outPath: String): String {
        val out = File(outPath)
        out.parentFile?.mkdirs()
        if (out.exists()) out.delete()

        val muxer = MediaMuxer(outPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val video = MediaExtractor().apply { setDataSource(videoPath) }
        val audio = MediaExtractor().apply { setDataSource(audioPath) }

        try {
            val videoTrack = selectTrack(video, "video/")
                ?: throw MuxException("no video track in the downloaded stream")
            val audioTrack = selectTrack(audio, "audio/")
                ?: throw MuxException("no audio track in the downloaded stream")

            val outVideo = muxer.addTrack(video.getTrackFormat(videoTrack))
            val outAudio = muxer.addTrack(audio.getTrackFormat(audioTrack))
            muxer.start()

            copy(video, videoTrack, muxer, outVideo)
            copy(audio, audioTrack, muxer, outAudio)

            muxer.stop()
        } finally {
            runCatching { muxer.release() }
            runCatching { video.release() }
            runCatching { audio.release() }
        }
        return outPath
    }

    /** Index of the first track whose mime starts with [prefix], and select it. */
    private fun selectTrack(extractor: MediaExtractor, prefix: String): Int? {
        for (i in 0 until extractor.trackCount) {
            val mime = extractor.getTrackFormat(i).getString(MediaFormat.KEY_MIME) ?: continue
            if (mime.startsWith(prefix)) {
                extractor.selectTrack(i)
                return i
            }
        }
        return null
    }

    private fun copy(extractor: MediaExtractor, trackIndex: Int, muxer: MediaMuxer, outIndex: Int) {
        val buffer = ByteBuffer.allocate(BUFFER_SIZE)
        val info = MediaCodec.BufferInfo()
        extractor.seekTo(0, MediaExtractor.SEEK_TO_CLOSEST_SYNC)
        while (true) {
            val size = extractor.readSampleData(buffer, 0)
            if (size < 0) break
            info.offset = 0
            info.size = size
            info.presentationTimeUs = extractor.sampleTime
            info.flags = extractor.sampleFlags
            muxer.writeSampleData(outIndex, buffer, info)
            extractor.advance()
        }
        extractor.unselectTrack(trackIndex)
    }
}
