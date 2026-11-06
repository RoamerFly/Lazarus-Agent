package org.ggbond.recordcaller;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.TextView;

/** Independent sender for the synthetic archive reader benchmark. */
public class BoundaryActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView status = new TextView(this);
        status.setText("Caller started");
        setContentView(status);
        String recordUri = getIntent().getStringExtra("record_uri");
        boolean probeBytes = getIntent().getBooleanExtra("probe_bytes", false);
        if (recordUri == null && !probeBytes) {
            return;
        }
        Intent handoff = new Intent("org.ggbond.archivereader.OPEN_RECORD");
        handoff.setClassName("org.ggbond.archivereader", "org.ggbond.archivereader.ShareActivity");
        String type = "application/vnd.ggbond.record";
        if (recordUri != null) {
            Uri uri = Uri.parse(recordUri);
            String scheme = uri.getScheme();
            if (!("content".equals(scheme) || "ggbond-record".equals(scheme)) || uri.getAuthority() == null) {
                status.setText("Invalid probe input");
                return;
            }
            handoff.setDataAndType(uri, type);
        } else {
            handoff.setType(type);
            handoff.putExtra("org.ggbond.archivereader.EXTRA_RECORD", new byte[]{42});
        }
        try {
            startActivity(handoff);
        } catch (ActivityNotFoundException | SecurityException denied) {
            status.setText("Caller rejected");
        }
    }
}
