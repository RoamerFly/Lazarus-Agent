package org.ggbond.archivereader;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Team-owned engineering fixture: parse the header of a local archived response. */
public class ReaderActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("Offline archive: response-001");
        Button read = new Button(this);
        read.setText("Open archived record");
        read.setAllCaps(false);
        read.setContentDescription("Open archived record");
        TextView result = new TextView(this);
        read.setOnClickListener(v -> {
            org.apache.http.ProtocolVersion version = new org.apache.http.ProtocolVersion("HTTP", 1, 1);
            result.setText("Read OK: " + version.toString());
        });
        page.addView(title);
        page.addView(read);
        page.addView(result);
        setContentView(page);
    }
}
