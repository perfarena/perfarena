public String removeDuplicateLetters(String s) {
    int[] lastIdx = new int[26];
    boolean[] visited = new boolean[26];
    Stack<Character> stack = new Stack<>();

    for (int i = 0; i < s.length(); i++) {
        lastIdx[s.charAt(i) - 'a'] = i;
    }

    for (int i = 0; i < s.length(); i++) {
        char c = s.charAt(i);
        if (visited[c - 'a']) continue;

        while (!stack.isEmpty() && c < stack.peek() && lastIdx[stack.peek() - 'a'] > i) {
            visited[stack.pop() - 'a'] = false;
        }

        stack.push(c);
        visited[c - 'a'] = true;
    }

    StringBuilder result = new StringBuilder();
    for (char ch : stack) {
        result.append(ch);
    }

    return result.toString();
}