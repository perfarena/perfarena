import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

public class DestroyedIntervals {
    public int[][] getDestroyedIntervals(int[] dataStream) {
        List<int[]> result = new ArrayList<>();
        Set<Integer> set = new TreeSet<>();
        int left = -1;
        int right = -1;

        for (int num : dataStream) {
            if (set.isEmpty() || right + 1 != num) {
                if (!set.isEmpty()) {
                    result.add(new int[]{left, right});
                }
                left = num;
            }
            right = num;
            set.add(num);
        }

        if (!set.isEmpty()) {
            result.add(new int[]{left, right});
        }

        return result.toArray(new int[0][]);
    }
}