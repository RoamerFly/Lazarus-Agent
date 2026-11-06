package org.ggbond.recordcaller;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.widget.TextView;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;

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

    /** Serves two synthetic, single-byte records under a different app's authority. */
    public static final class SyntheticRecordProvider extends ContentProvider {
        @Override public boolean onCreate() { return true; }

        @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
            String path = uri.getPath();
            if (!"r".equals(mode) || !("/record".equals(path) || "/other".equals(path))) {
                throw new FileNotFoundException("Unknown synthetic record");
            }
            boolean matching = "/record".equals(path);
            File data = new File(getContext().getCacheDir(), matching ? "probe-42" : "probe-41");
            try (FileOutputStream out = new FileOutputStream(data)) {
                out.write(matching ? 42 : 41);
            } catch (IOException error) {
                throw new FileNotFoundException("Cannot create synthetic record");
            }
            return ParcelFileDescriptor.open(data, ParcelFileDescriptor.MODE_READ_ONLY);
        }

        @Override public String getType(Uri uri) { return "application/vnd.ggbond.record"; }
        @Override public Cursor query(Uri uri, String[] fields, String selection, String[] args, String order) {
            return null;
        }
        @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
        @Override public int update(Uri uri, ContentValues values, String selection, String[] args) {
            throw new UnsupportedOperationException();
        }
        @Override public int delete(Uri uri, String selection, String[] args) {
            throw new UnsupportedOperationException();
        }
    }
}
