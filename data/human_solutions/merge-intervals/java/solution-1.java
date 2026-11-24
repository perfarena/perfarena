// Define base case
if (intervals.length <= 1) {
    return intervals;
}

// Sort intervals by start time
Arrays.sort(intervals, (a, b) -> Integer.compare(a[0], b[0]));

// Create a stack to store non-overlapping intervals
Stack<int[]> stack = new Stack<>();
stack.add(intervals[0]);

// Merge overlapping intervals
for (int i = 1; i < intervals.length; i++) {
    int[] prev = stack.peek();
    int[] current = intervals[i];
    
    if (prev[1] >= current[0]) {
        prev[1] = Math.max(prev[1], current[1]);
    } else {
        stack.add(current);
    }
}

// Convert stack to array of non-overlapping intervals
int[][] result = new int[stack.size()][2];
for (int i = stack.size() - 1; i >= 0; i--) {
    result[i] = stack.pop();
}

return result;