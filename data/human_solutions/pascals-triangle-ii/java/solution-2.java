import java.util.ArrayList;
import java.util.List;

public class PascalTriangle {
    public List<Integer> getRow(int rowIndex) {
        List<Integer> previous = new ArrayList<>();
        previous.add(1);

        for (int r = 1; r <= rowIndex; r++) {
            List<Integer> current = new ArrayList<>();
            current.add(1);

            for (int i = 1; i < previous.size(); i++) {
                current.add(previous.get(i - 1) + previous.get(i));
            }

            current.add(1);
            previous = current;
        }

        return previous;
    }
}