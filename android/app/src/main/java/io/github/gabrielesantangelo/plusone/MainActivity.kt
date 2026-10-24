package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var ink: InkSurface

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ink = InkSurface(this)
        val hint = TextView(this).apply {
            setText(R.string.ink_hint)
            setTextColor(Color.DKGRAY)
            textSize = 14f
            setPadding(24, 16, 24, 16)
        }
        setContentView(FrameLayout(this).apply {
            addView(ink)
            addView(hint, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        })
    }

    override fun onDestroy() {
        ink.release()
        super.onDestroy()
    }
}
