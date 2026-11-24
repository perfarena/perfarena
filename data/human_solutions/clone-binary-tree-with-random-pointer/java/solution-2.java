import java.util.HashMap;

public class LongestSubstring {
    public int longestSubstring(String s) {
        HashMap<Character, Integer> map = new HashMap<>();
        int Max = -1;

        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);

            if (map.containsKey(ch)) {
                Max = Math.max(Max, i - map.get(ch) - 1);
            } else {
                map.put(ch, i);
            }
        }

        return Max;
    }
}