package org.ggbond.umbrelladesk;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class LoanActivity extends Activity {
    private int borrowed;
    private TextView count;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(24, 24, 24, 24);

        TextView heading = new TextView(this);
        heading.setText(R.string.heading);
        heading.setTextSize(22);
        count = new TextView(this);
        page.addView(heading);
        page.addView(count);

        Button borrow = new Button(this);
        borrow.setAllCaps(false);
        borrow.setText(R.string.borrow);
        borrow.setOnClickListener(view -> {
            borrowed++;
            showCount();
        });
        page.addView(borrow);

        Button returnUmbrella = new Button(this);
        returnUmbrella.setAllCaps(false);
        returnUmbrella.setText(R.string.return_umbrella);
        returnUmbrella.setOnClickListener(view -> {
            borrowed = Math.max(0, borrowed - 1);
            showCount();
        });
        page.addView(returnUmbrella);

        setContentView(page);
        showCount();
    }

    private void showCount() {
        count.setText(getString(R.string.loan_count, borrowed));
    }
}
