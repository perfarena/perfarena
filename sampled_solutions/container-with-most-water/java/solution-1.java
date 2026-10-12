int maxArea = 0;
for (int i = 0; i < height.length - 1; i++) {
    for (int j = i + 1; j < height.length; j++) {
        int minHeight = Math.min(height[i], height[j]);
        int distance = j - i;
        int currentArea = minHeight * distance;
        maxArea = Math.max(maxArea, currentArea);
    }
}
return maxArea;