import java.util.ArrayList;
import java.util.List;

public class PartitionLabels {
    public List<Integer> partitionLabels(String s) {
        List<Integer> result = new ArrayList<>();
        int[] lastIndex = new int[26];

        for (int i = 0; i < s.length(); i++) {
            lastIndex[s.charAt(i) - 'a'] = i;
        }

        int start = 0, last = 0;
        for (int i = 0; i < s.length(); i++) {
            last = Math.max(last, lastIndex[s.charAt(i) - 'a']);
            if (last == i) {
                result.add(last - start + 1);
                start = i + 1;
            }
        }

        return result;
    }
}