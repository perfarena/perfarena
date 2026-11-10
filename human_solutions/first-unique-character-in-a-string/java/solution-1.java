import java.util.HashMap;

public class FirstUniqueCharacter {
    public int firstUniqueCharIndex(String s) {
        HashMap<Character, Integer> map = new HashMap<>();

        for (int i = 0; i < s.length(); i++) {
            char current = s.charAt(i);
            if (map.containsKey(current)) {
                map.put(current, -1); // Not unique, mark as -1
            } else {
                map.put(current, i); // Unique, store index
            }
        }

        int unique = Integer.MAX_VALUE;
        for (char c : map.keySet()) {
            if (map.get(c) != -1 && map.get(c) < unique) {
                unique = map.get(c);
            }
        }

        return unique == Integer.MAX_VALUE ? -1 : unique;
    }

    public static void main(String[] args) {
        FirstUniqueCharacter solution = new FirstUniqueCharacter();
        String input = "leetcode";
        System.out.println("Index of first unique character: " + solution.firstUniqueCharIndex(input));
    }
}