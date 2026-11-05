package org.ggbond.fieldroute;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Team-owned, offline example of a maintenance operator's route sheet. */
public final class RouteActivity extends Activity {
    private TextView current;
    private TextView instructions;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(24, 24, 24, 24);

        TextView heading = new TextView(this);
        heading.setText("Field route sheet");
        heading.setTextSize(22);
        page.addView(heading);
        current = new TextView(this);
        instructions = new TextView(this);
        addRoute(page, "Open north route", "north");
        addRoute(page, "Open south route", "south");
        page.addView(current);
        page.addView(instructions);
        setContentView(page);

        showRoute("north");
    }

    private void addRoute(LinearLayout page, String label, String route) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setOnClickListener(view -> showRoute(route));
        page.addView(button);
    }

    private void showRoute(String route) {
        boolean south = "south".equals(route);
        current.setText(south ? "Active route: South" : "Active route: North");
        instructions.setText(south ? "Inspect the pump seal" : "Check the valve position");
    }
}
