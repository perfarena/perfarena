Set<Character> seen = new HashSet<>();
int longestSubstring = 0;
int windowStart = 0;

for (int windowEnd = 0; windowEnd < s.length(); windowEnd++) {
    char c = s.charAt(windowEnd);
    
    if (!seen.contains(c)) {
        seen.add(c);
        longestSubstring = Math.max(longestSubstring, windowEnd - windowStart + 1);
    } else {
        while (seen.contains(c)) {
            seen.remove(s.charAt(windowStart));
            windowStart++;
        }
        seen.add(c);
    }
}

return longestSubstring;