int i = 0, j = 0;
while (i < s.length() && j < t.length()) {
    if (s.charAt(i) == t.charAt(j)) {
        i++; // Move to the next character in s
    }
    j++; // Move to the next character in t
}
return i == s.length(); // Return true if all characters in s are found in t

// Time Complexity: O(n) where n is the size of input parameter t
// Space Complexity: O(1)