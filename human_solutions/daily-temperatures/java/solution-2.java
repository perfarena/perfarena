public int[] dailyTemperatures(int[] t) {
    Stack<Integer> stack = new Stack<>();
    int[] map = new int[t.length];

    for (int i = 0; i < t.length; i++) {
        while (!stack.isEmpty() && t[i] > t[stack.peek()]) {
            int top = stack.pop();
            map[top] = i - top;
        }
        stack.push(i);
    }

    while (!stack.isEmpty()) {
        int top = stack.pop();
        map[top] = 0;
    }

    return map;
}