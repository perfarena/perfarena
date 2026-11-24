public String reverseWords(String s) {
    // Trim the input string to remove extra spaces
    s = s.trim();
    
    // Split the string into an array of words
    String[] words = s.split("\\s+");
    
    // Reverse the order of words in the array
    StringBuilder reversed = new StringBuilder();
    for (int i = words.length - 1; i >= 0; i--) {
        if (words[i].length() > 0) {
            reversed.append(words[i]).append(" ");
        }
    }
    
    // Remove the extra space at the end and return the reversed string
    return reversed.toString().trim();
}