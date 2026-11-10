public int lengthOfLongestSubstring(String s) {
    int maxLen = 0;
    Set<Character> set = new HashSet<>();
    int i = 0, j = 0;
    
    while (j < s.length()) {
        if (!set.contains(s.charAt(j))) {
            set.add(s.charAt(j));
            j++;
            maxLen = Math.max(maxLen, j - i);
        } else {
            set.remove(s.charAt(i));
            i++;
        }
    }
    
    return maxLen;
}