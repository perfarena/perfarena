public String getFirstKWords(String s, int K) {
    // Step 1: Split the input string into an array of words
    String[] words = s.split(" ");
    
    // Step 2: Create a StringBuilder
    StringBuilder result = new StringBuilder();
    
    // Step 3: Append the first K words to the StringBuilder
    for (int i = 0; i < K && i < words.length; i++) {
        result.append(words[i]).append(" ");
    }
    
    // Step 4: Convert StringBuilder to a string and return
    String finalResult = result.toString().trim();
    
    return finalResult;
}