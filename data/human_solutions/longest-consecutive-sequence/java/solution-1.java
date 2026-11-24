import java.util.HashMap;

public class LongestConsecutiveSequence {
    public int longestConsecutive(int[] nums) {
        HashMap<Integer, Boolean> map = new HashMap<>();
        for (int num : nums) {
            map.put(num, true);
        }

        int max = 0;
        for (int num : nums) {
            if (!map.containsKey(num - 1)) {
                int currentNum = num;
                int currentMax = 1;

                while (map.containsKey(currentNum + 1)) {
                    currentNum++;
                    currentMax++;
                }

                max = Math.max(max, currentMax);
            }
        }

        return max;
    }
}