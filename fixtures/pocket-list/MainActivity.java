package org.ggbond.pocketlist;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        TextView heading = new TextView(this);
        heading.setText(R.string.heading);
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(R.string.add_item);
        button.setOnClickListener(view -> heading.setText(R.string.done));
        page.addView(heading);
        page.addView(button);
        setContentView(page);
    }
}
