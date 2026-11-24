import java.util.ArrayList;
import java.util.List;

public List<Integer> generatePascalsTriangleRow(int n) {
    List<Integer> row = new ArrayList<>(n + 1);
    for (int i = 1; i <= n; i++) {
        for (int j = i - 1; j > 0; j--) {
            row.set(j, row.get(j) + row.get(j - 1));
        }
    }
    return row;
}