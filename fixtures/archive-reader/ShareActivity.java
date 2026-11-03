package org.ggbond.archivereader;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Button;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;

/** Reproduce an old file-sharing implementation that exposes a file URI. */
public class ShareActivity extends Activity {
    private static final String OPEN_RECORD = "org.ggbond.archivereader.OPEN_RECORD";
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (OPEN_RECORD.equals(getIntent().getAction())) {
            Uri uri = getIntent().getData();
            TextView result = new TextView(this);
            try (InputStream stream = getContentResolver().openInputStream(uri)) {
                result.setText(stream.read() == 42 ? "Shared" : "Unexpected file");
            } catch (IOException e) {
                result.setText("Cannot read record");
            }
            setContentView(result);
            return;
        }
        Button share = new Button(this);
        share.setText("Share local record");
        share.setAllCaps(false);
        share.setOnClickListener(view -> {
            File record = new File(getFilesDir(), "record.txt");
            try (FileOutputStream out = new FileOutputStream(record)) {
                out.write(42);
            } catch (IOException e) {
                share.setText("Cannot write record");
                return;
            }
            Intent intent = new Intent(OPEN_RECORD);
            intent.setDataAndType(Uri.fromFile(record), "application/vnd.ggbond.record");
            startActivity(intent);
        });
        setContentView(share);
    }
}
