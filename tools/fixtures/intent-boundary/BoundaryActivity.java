package org.ggbond.intentboundaryprobe;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Process;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.IOException;
import java.io.InputStream;

/** Independent app for reading one synthetic test marker on a disposable AVD. */
public class BoundaryActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Uri data = getIntent().getData();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        TextView result = new TextView(this);
        TextView grant = new TextView(this);
        layout.addView(result);
        layout.addView(grant);
        if (data == null || !"content".equals(data.getScheme())
                || !"org.ggbond.archivereader.records".equals(data.getAuthority())
                || !"/record.txt".equals(data.getPath())) {
            result.setText("PROBE_UNEXPECTED_URI");
        } else {
            boolean allowed = checkUriPermission(data, Process.myPid(), Process.myUid(),
                    Intent.FLAG_GRANT_READ_URI_PERMISSION) == PackageManager.PERMISSION_GRANTED;
            grant.setText(allowed ? "PROBE_GRANT_ALLOWED" : "PROBE_GRANT_DENIED");
            try (InputStream stream = getContentResolver().openInputStream(data)) {
                result.setText(stream != null && stream.read() == 42 ? "PROBE_READ_42" : "PROBE_UNEXPECTED_VALUE");
            } catch (SecurityException denied) {
                result.setText("PROBE_ACCESS_DENIED");
            } catch (IOException unavailable) {
                result.setText("PROBE_DATA_UNAVAILABLE");
            }
        }
        setContentView(layout);
    }
}
