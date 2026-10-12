int trapRainWater(vector<int>& height) {
    int n = height.size();
    vector<int> leftBoundary(n);
    vector<int> rightBoundary(n);

    // Compute left boundary heights
    for (int i = 0; i < n; i++) {
        if (i == 0) {
            leftBoundary[i] = height[i];
        } else {
            leftBoundary[i] = max(leftBoundary[i - 1], height[i]);
        }
    }

    // Compute right boundary heights
    for (int i = n - 1; i >= 0; i--) {
        if (i == n - 1) {
            rightBoundary[i] = height[i];
        } else {
            rightBoundary[i] = max(rightBoundary[i + 1], height[i]);
        }
    }

    int area = 0;
    for (int i = 0; i < n; i++) {
        area += min(leftBoundary[i], rightBoundary[i]) - height[i];
    }

    return area;
}