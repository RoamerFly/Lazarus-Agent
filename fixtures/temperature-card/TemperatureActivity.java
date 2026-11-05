package org.ggbond.temperaturecard;

import android.app.Activity;
import android.os.Bundle;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class TemperatureActivity extends Activity {
    private EditText temperature;
    private TextView result;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(24, 24, 24, 24);
        page.setFocusableInTouchMode(true);

        TextView heading = new TextView(this);
        heading.setText("Warehouse temperature");
        heading.setTextSize(22);
        page.addView(heading);

        TextView units = new TextView(this);
        units.setText("Units: Celsius");
        page.addView(units);

        temperature = new EditText(this);
        temperature.setHint("Enter degrees Celsius");
        temperature.setContentDescription("Temperature input");
        temperature.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_SIGNED);
        page.addView(temperature);

        Button calculate = new Button(this);
        calculate.setAllCaps(false);
        calculate.setText("Calculate");
        calculate.setOnClickListener(view -> calculateTemperature());
        page.addView(calculate);

        result = new TextView(this);
        result.setText("No result");
        page.addView(result);
        setContentView(page);
        page.requestFocus();
    }

    private void calculateTemperature() {
        try {
            int celsius = Integer.parseInt(temperature.getText().toString().trim());
            result.setText("Result: " + celsius + " °C");
            temperature.setText("");
        } catch (NumberFormatException invalid) {
            result.setText("Enter a whole number");
        }
    }
}
