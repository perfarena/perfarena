// Java program to find the largest substring between two equal characters
import java.util.HashMap;

public class LargestSubstring {
    public static int largestSubstring(String s) {
        HashMap<Character, Integer> map = new HashMap<>();
        int Max = -1;

        for (int right = 0; right < s.length(); right++) {
            char ch = s.charAt(right);
            if (map.containsKey(ch)) {
                int left = map.get(ch);
                int length = right - left - 1;
                Max = Math.max(Max, length);
            } else {
                map.put(ch, right);
            }
        }

        return Max;
    }

    public static void main(String[] args) {
        String s = "abcda";
        System.out.println(largestSubstring(s)); // Output: 2
    }
}