public List<Integer> getRow(int rowIndex) {
    List<Integer> row = new ArrayList<>();
    for (int i = 0; i <= rowIndex; i++) {
        row.add(0, 1); // Add 1 at the beginning of the row
        for (int j = 1; j < row.size() - 1; j++) {
            row.set(j, row.get(j) + row.get(j + 1)); // Update values in the row
        }
    }
    return row;
}