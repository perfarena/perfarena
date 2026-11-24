public int lengthOfLongestSubstring(String s) {
    Map<Character, Integer> map = new HashMap<>();
    int longest = 0, i = 0, j = 0;
    
    while (j < s.length()) {
        if (map.containsKey(s.charAt(j))) {
            i = Math.max(i, map.get(s.charAt(j)) + 1);
        }
        map.put(s.charAt(j), j);
        longest = Math.max(longest, j - i + 1);
        j++;
    }
    
    return longest;
}