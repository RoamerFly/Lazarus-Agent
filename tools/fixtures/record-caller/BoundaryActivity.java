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
        Intent handoff = new Intent("org.ggbond.archivereader.OPEN_RECORD");
        handoff.setClassName("org.ggbond.archivereader", "org.ggbond.archivereader.ShareActivity");
        String type = "application/vnd.ggbond.record";
        if (getIntent().getBooleanExtra("probe_record_uri", false)) {
            String requestedPath = getIntent().getStringExtra("record_path");
            String path = "/record".equals(requestedPath) ? "/record" : "/record.txt";
            handoff.setDataAndType(Uri.parse("content://org.ggbond.archivereader.records" + path), type);
        } else {
            handoff.setType(type);
            handoff.putExtra("org.ggbond.archivereader.EXTRA_RECORD", new byte[]{42});
        }
        TextView status = new TextView(this);
        status.setText("Caller started");
        setContentView(status);
        try {
            startActivity(handoff);
        } catch (ActivityNotFoundException | SecurityException denied) {
            status.setText("Caller rejected");
        }
    }
}
