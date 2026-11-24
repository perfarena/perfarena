// Initialize return variable
int maxArea = 0;

// Validate input array
if (heights != null && heights.Length > 0) {
    Stack<int> stack = new Stack<int>();
    stack.Push(0); // Start with the first index

    for (int i = 1; i < heights.Length; i++) {
        while (stack.Count > 0 && heights[i] < heights[stack.Peek()]) {
            int top = stack.Pop();
            int width = stack.Count == 0 ? i : i - stack.Peek() - 1;
            int area = heights[top] * width;
            maxArea = Math.Max(maxArea, area);
        }
        stack.Push(i);
    }

    while (stack.Count > 0) {
        int top = stack.Pop();
        int width = stack.Count == 0 ? heights.Length : heights.Length - stack.Peek() - 1;
        int area = heights[top] * width;
        maxArea = Math.Max(maxArea, area);
    }
}

return maxArea;