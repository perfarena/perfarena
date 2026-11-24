int maxLen = -1;
for (int i = 0; i < s.length(); i++) {
    char currentChar = s.charAt(i);
    int lastIndex = s.lastIndexOf(currentChar);
    
    if (lastIndex != i) {
        int length = lastIndex - i - 1;
        maxLen = Math.max(maxLen, length);
    }
}
return maxLen;