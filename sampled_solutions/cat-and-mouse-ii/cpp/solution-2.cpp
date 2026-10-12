// Take input for matrix size m x n
int m = matrix.size();
int n = matrix[0].size();

// Initialize matrix with zeros
vector<vector<int>> matrix(m, vector<int>(n, 0));

// Initialize variable to store the answer
int answer = 0;

// Create a height vector to store the count of '1's in each column
vector<int> height(n, 0);

// Traverse through the matrix to populate the height vector
for (int i = 0; i < m; i++) {
    for (int j = 0; j < n; j++) {
        if (matrix[i][j] == 1) {
            height[j]++;
        }
    }
}

// Sort the height vector
sort(height.begin(), height.end());

// Calculate the maximum area
for (int j = 0; j < n; j++) {
    answer = max(answer, height[j] * (n - j));
}

// Return the maximum area
return answer;