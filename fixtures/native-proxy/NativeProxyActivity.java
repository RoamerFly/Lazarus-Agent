package org.ggbond.nativeproxy;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Team-owned fixture: the bad proxy reaches native code only after a user click. */
public final class NativeProxyActivity extends Activity {
  static { System.loadLibrary("nativeproxy"); }
  private static native int nativeRun();
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    LinearLayout page=new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
    TextView title=new TextView(this); title.setText("Embedded native payload verification");
    Button run=new Button(this); run.setText("Run native operation"); run.setAllCaps(false);
    run.setContentDescription("Run native operation");
    TextView result=new TextView(this); result.setText("Ready: native operation has not run");
    run.setOnClickListener(v -> {
      int code=nativeRun();
      if(code==7) result.setText("Native payload completed successfully");
      else result.setText("Unexpected native return: "+code);
    });
    page.addView(title); page.addView(run); page.addView(result); setContentView(page);
  }
}
