package org.ggbond.teatimer;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private int brews;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        TextView heading = new TextView(this);
        heading.setText(R.string.screen_name);
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(R.string.start_brew);
        button.setOnClickListener(view -> heading.setText(getString(R.string.active_message, ++brews)));
        page.addView(heading);
        page.addView(button);
        setContentView(page);
    }
}
