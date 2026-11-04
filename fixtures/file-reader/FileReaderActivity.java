package org.ggbond.filereader;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

/**
 * Team-owned fixture for the generic behaviour-modification flow.
 *
 * The intentionally old behaviour always opens record-a.txt at startup. Both
 * buttons call the same private instance method with a single String argument,
 * allowing a repair to persist and restore the selected record without
 * depending on this package, class name, label, or record contents.
 */
public final class FileReaderActivity extends Activity implements View.OnClickListener {
    private TextView current;
    private TextView contents;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);

        current = new TextView(this);
        contents = new TextView(this);
        Button recordA = createButton("Open record A", "record-a.txt");
        Button recordB = createButton("Open record B", "record-b.txt");

        page.addView(current);
        page.addView(recordA);
        page.addView(recordB);
        page.addView(contents);
        setContentView(page);

        openRecord("record-a.txt");
    }

    private Button createButton(String label, String recordName) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTag(recordName);
        button.setContentDescription(label);
        button.setOnClickListener(this);
        return button;
    }

    @Override public void onClick(View view) {
        openRecord((String) view.getTag());
    }

    private void openRecord(String recordName) {
        current.setText("Current record: " + recordName);
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(getAssets().open(recordName), "UTF-8"));
            contents.setText(reader.readLine());
            reader.close();
        } catch (IOException error) {
            contents.setText("Could not read selected record");
        }
    }
}
