public boolean isPalindrome(String s) {
    if (s.length() <= 1) {
        return true; // Empty string or single character is a palindrome
    }

    int start = 0, end = s.length() - 1;
    while (start < end) {
        while (start < end && !isAlNum(s.charAt(start))) {
            start++;
        }
        while (start < end && !isAlNum(s.charAt(end))) {
            end--;
        }

        if (Character.toLowerCase(s.charAt(start)) != Character.toLowerCase(s.charAt(end))) {
            return false;
        }

        start++;
        end--;
    }
    return true;
}

private boolean isAlNum(char c) {
    return Character.isLetterOrDigit(c);
}