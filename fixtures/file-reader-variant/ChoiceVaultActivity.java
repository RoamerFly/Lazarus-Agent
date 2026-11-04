package org.ggbond.choicevault;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

/** A separate package and control vocabulary for the generic repair check. */
public final class ChoiceVaultActivity extends Activity implements View.OnClickListener {
    private TextView selection;
    private TextView body;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        selection = new TextView(this);
        body = new TextView(this);
        Button east = button("Review east memo", "memo-east.txt");
        Button west = button("Review west memo", "memo-west.txt");
        page.addView(selection);
        page.addView(east);
        page.addView(west);
        page.addView(body);
        setContentView(page);
        showMemo("memo-east.txt");
    }

    private Button button(String label, String memo) {
        Button view = new Button(this);
        view.setText(label);
        view.setAllCaps(false);
        view.setTag(memo);
        view.setContentDescription(label);
        view.setOnClickListener(this);
        return view;
    }

    @Override public void onClick(View view) {
        showMemo((String) view.getTag());
    }

    private void showMemo(String memo) {
        selection.setText("Selected memo: " + memo);
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(getAssets().open(memo), "UTF-8"));
            body.setText(reader.readLine());
            reader.close();
        } catch (IOException error) {
            body.setText("Could not read selected memo");
        }
    }
}
