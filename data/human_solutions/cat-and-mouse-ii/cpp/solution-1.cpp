vector<vector<int>> ones; // Vector to store counts of consecutive ones for each row

for each column in matrix {
    int dp0 = 0; // Consecutive ones count for the current column
    for each row in matrix {
        if current value is 0 {
            dp0 = 0; // Reset count
        } else {
            dp0++; // Increment count
        }
        ones[current row].push_back(dp0); // Store the count
    }
}

int maxArea = 0; // Initialize maximum area

for each row in ones {
    sort(row, greater<int>()); // Sort counts in reverse order
    int rowArea = 0; // Initialize area for the current row
    for each count in row {
        rowArea = max(rowArea, count * (index + 1)); // Calculate area
    }
    maxArea = max(maxArea, rowArea); // Update maximum area
}

return maxArea; // Return the final maximum area