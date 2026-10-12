public String removeAdjacentDuplicates(String s) {
    Stack<Character> stack = new Stack<>();
    
    for (char c : s.toCharArray()) {
        if (stack.isEmpty() || stack.peek() != c) {
            stack.push(c);
        } else {
            stack.pop();
        }
    }
    
    char[] result = new char[stack.size()];
    int i = stack.size() - 1;
    while (!stack.isEmpty()) {
        result[i--] = stack.pop();
    }
    
    return new String(result);
}