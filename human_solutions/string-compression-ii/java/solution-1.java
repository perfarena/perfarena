// Sort the array based on x-axis
Arrays.sort(points, (a, b) -> a[0] - b[0]);

int answer = 0;
for (int i = 1; i < n; i++) {
    // Calculate the maximum width
    answer = Math.max(answer, points[i][0] - points[i - 1][0]);
}

return answer;