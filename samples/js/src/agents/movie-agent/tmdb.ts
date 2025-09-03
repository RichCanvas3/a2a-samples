/**
 * Utility function to call the TMDB API
 * @param endpoint The TMDB API endpoint (e.g., 'movie', 'person')
 * @param query The search query
 * @returns Promise that resolves to the API response data
 */
export async function callTmdbApi(endpoint: string, query: string) {
  // Support either TMDB v3 API key or v4 bearer token
  const apiKey = process.env.TMDB_API_KEY;
  const apiToken = process.env.TMDB_API_TOKEN; // optional v4 token
  if (!apiKey && !apiToken) {
    throw new Error("TMDB_API_KEY or TMDB_API_TOKEN environment variable must be set");
  }

  try {
    // Make request to TMDB API
    const url = new URL(`https://api.themoviedb.org/3/search/${endpoint}`);
    console.log("***************** use api key: ", apiKey);
    if (apiKey) {
      url.searchParams.append("api_key", apiKey);
    }
    url.searchParams.append("query", query);
    url.searchParams.append("include_adult", "false");
    url.searchParams.append("language", "en-US");
    url.searchParams.append("page", "1");

    const response = await fetch(url.toString(), {
      headers: apiToken ? { Authorization: `Bearer ${apiToken}` } : undefined,
    });

    if (!response.ok) {
      throw new Error(
        `TMDB API error: ${response.status} ${response.statusText}`
      );
    }

    return await response.json();
  } catch (error) {
    console.error(`Error calling TMDB API (${endpoint}):`, error);
    throw error;
  }
}
