import { callTmdbApi } from "./tmdb.js";

export const openAiToolDefinitions = [
  {
    type: "function",
    function: {
      name: "searchMovies",
      description: "Search TMDB for movies by title",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Movie title to search for" },
        },
        required: ["query"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "searchPeople",
      description: "Search TMDB for people by name",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Person name to search for" },
        },
        required: ["query"],
        additionalProperties: false,
      },
    },
  },
];

export async function searchMoviesTool({ query }: { query: string }) {
  console.log("[tmdb:searchMovies]", JSON.stringify(query));
  try {
    const data = await callTmdbApi("movie", query);

    const results = data.results.map((movie: any) => {
      if (movie.poster_path) {
        movie.poster_path = `https://image.tmdb.org/t/p/w500${movie.poster_path}`;
      }
      if (movie.backdrop_path) {
        movie.backdrop_path = `https://image.tmdb.org/t/p/w500${movie.backdrop_path}`;
      }
      return movie;
    });

    return { ...data, results };
  } catch (error) {
    console.error("Error searching movies:", error);
    throw error;
  }
}

export async function searchPeopleTool({ query }: { query: string }) {
  console.log("[tmdb:searchPeople]", JSON.stringify(query));
  try {
    const data = await callTmdbApi("person", query);

    const results = data.results.map((person: any) => {
      if (person.profile_path) {
        person.profile_path = `https://image.tmdb.org/t/p/w500${person.profile_path}`;
      }
      if (person.known_for && Array.isArray(person.known_for)) {
        person.known_for = person.known_for.map((work: any) => {
          if (work.poster_path) {
            work.poster_path = `https://image.tmdb.org/t/p/w500${work.poster_path}`;
          }
          if (work.backdrop_path) {
            work.backdrop_path = `https://image.tmdb.org/t/p/w500${work.backdrop_path}`;
          }
          return work;
        });
      }
      return person;
    });

    return { ...data, results };
  } catch (error) {
    console.error("Error searching people:", error);
    throw error;
  }
}

export const openAiToolHandlers: Record<string, (args: any) => Promise<any>> = {
  searchMovies: (args: any) => searchMoviesTool(args),
  searchPeople: (args: any) => searchPeopleTool(args),
};
