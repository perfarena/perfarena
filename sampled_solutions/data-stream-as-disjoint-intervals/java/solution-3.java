// Create an ArrayList to store intervals
List<int[]> intervals = new ArrayList<>();

// Iterate through the stream to find intervals
while (!tree.isEmpty()) {
    int start = tree.first();
    int end = start;

    // Iterate to find continuous intervals
    for (int value : tree.tailSet(start)) {
        if (value - end == 1) {
            end = value;
        } else {
            break;
        }
    }

    // Add the interval to the list
    intervals.add(new int[]{start, end});

    // Update start and end for the next interval
    tree = tree.tailSet(end + 1);
}

// Convert intervals to array
int[][] result = intervals.toArray(new int[0][]);

// Return the intervals
return result;