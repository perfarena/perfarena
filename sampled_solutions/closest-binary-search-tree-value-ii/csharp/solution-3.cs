Q<int> Q = new Q<int>();

List<int> FindClosestValues(Node root, double Target, int K) {
    DFS(root, Target, K, Q);
    return Q.ToList();
}