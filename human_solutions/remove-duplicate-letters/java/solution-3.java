public String removeDuplicateLetters(String s) {
    StringBuilder result = new StringBuilder();
    int[] count = new int[26];
    boolean[] used = new boolean[26];
    
    char[] chars = s.toCharArray();
    
    for (char c : chars) {
        count[c - 'a']++;
    }
    
    for (char c : chars) {
        count[c - 'a']--;
        
        if (used[c - 'a']) {
            continue;
        }
        
        while (result.length() > 0 && c < result.charAt(result.length() - 1) && count[result.charAt(result.length() - 1) - 'a'] > 0) {
            used[result.charAt(result.length() - 1) - 'a'] = false;
            result.deleteCharAt(result.length() - 1);
        }
        
        result.append(c);
        used[c - 'a'] = true;
    }
    
    return result.toString();
}