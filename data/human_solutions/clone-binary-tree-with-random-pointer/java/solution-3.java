public class Main {
    public static void main(String[] args) {
        LongestSubstring ls = new LongestSubstring();

        // Test cases
        System.out.println(ls.longestSubstring("aa")); // Output: -1
        System.out.println(ls.longestSubstring("abc")); // Output: 1
        System.out.println(ls.longestSubstring("xyz")); // Output: -1
    }
}