import { QrCode } from '../components/QrCode'
import { CAPTURE_SHEET } from '../lib/captureSheet'
import { DEFAULT_OFFSET_MM, RASTER_PX_PER_MM } from '../lib/pipeline/runPipeline'

export function AProposPage() {
  const url = typeof window !== 'undefined' ? window.location.href.split('#')[0] : ''

  return (
    <div className="stack">
      <section className="card" style={{ textAlign: 'center' }}>
        <h1>OptiFrame</h1>
        <p>Scannez pour ouvrir l'app sur un autre téléphone. Aucune installation, aucun compte.</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <QrCode value={url} />
        </div>
        <p className="muted" style={{ wordBreak: 'break-all' }}>{url}</p>
      </section>

      <section className="card">
        <h3>Dispositif de capture</h3>
        <p>
          Une feuille de papier. <a href="capture-sheet.pdf" download>Téléchargez-la</a> et
          imprimez-la sur du A4 — <strong>l'échelle n'a pas d'importance</strong>. Mesurez le trait
          de contrôle au pied à coulisse et saisissez sa vraie longueur dans l'onglet Capturer : la
          plupart des imprimantes réduisent la page sans le dire, et sans cette mesure toutes les
          valeurs seraient fausses du même facteur, silencieusement. Avec elle, n'importe quelle
          imprimante convient.
        </p>
        <p>
          Les quatre marqueurs ArUco ({CAPTURE_SHEET.markerSizeMm} mm, dictionnaire{' '}
          {CAPTURE_SHEET.dictionary}) donnent l'échelle et la perspective. Quatre plutôt qu'un :
          cela fournit seize correspondances au lieu de quatre, ce qui conditionne bien mieux le
          calcul sur toute la feuille et permet qu'un marqueur soit masqué par une main sans perdre
          la mesure.
        </p>
        <p>
          La zone de pose est <strong>grise et unie</strong>, et c'est un choix. Nous avons d'abord
          imprimé un damier fin, dans l'idée de repérer le verre à la réfraction du motif. La
          physique ne suit pas : posé sur le papier, un verre voit son fond à distance quasi nulle,
          et un verre de 4 dioptries à 2 mm du papier ne décale le motif que d'une vingtaine de
          micromètres. Le motif n'apportait rien et masquait le seul indice réellement fort, le bord
          du verre.
        </p>
      </section>

      <section className="card">
        <h3>Choix techniques</h3>
        <ul className="tight">
          <li>
            <strong>Aucun OpenCV.js.</strong> Homographie, contours sous-pixel et rééchantillonnage
            sont écrits en TypeScript. Le chargement initial passe de 10,2 Mo à environ 420 Ko.
          </li>
          <li>
            <strong>Tout dans un Web Worker.</strong> L'interface reste utilisable pendant la
            mesure — faites défiler cette page pendant un calcul pour le vérifier.
          </li>
          <li>
            <strong>La photo n'est pas redressée pour être mesurée.</strong> Seule la zone de pose
            l'est, à {RASTER_PX_PER_MM} px/mm.
          </li>
          <li>
            <strong>Le bord mesure, le modèle oriente.</strong> Un tracé de crête fermée par
            programmation dynamique place le contour au sous-pixel ; le U-Net lui dit où chercher.
          </li>
          <li>
            <strong>Un seul réglage calibré</strong> dans toute la chaîne : une correction radiale,
            ajustée au pied à coulisse — et elle vaut {DEFAULT_OFFSET_MM.toFixed(1)} mm, c'est-à-dire
            aucune. Le contour tombe là où est le verre parce que la géométrie est juste, pas parce
            qu'une constante a été réglée jusqu'à ce que ça tombe juste.
          </li>
        </ul>
      </section>

      <section className="card">
        <h3>Données et IA</h3>
        <p>
          Jeu de données entièrement produit par nous : rendus synthétiques physiquement motivés
          (biseau du bord, perte de Fresnel, reflets, ombre portée) plus des photos de nos propres
          verres étiquetées à la main. <strong>Aucune donnée personnelle</strong>, aucun modèle
          pré-entraîné, donc aucune licence tierce à citer sur les données.
        </p>
        <p>
          Modèle : U-Net d'environ 150 000 paramètres, entraîné de zéro, exécuté par TensorFlow.js
          sur le backend WebAssembly. Bibliothèques : js-aruco2 (MIT), manifold-3d, three.js,
          TensorFlow.js.
        </p>
      </section>

      <section className="card">
        <h3>Limites reconnues</h3>
        <ul className="tight">
          <li>
            L'app <strong>refuse</strong> une capture dont le bord est trop faible plutôt que
            d'annoncer un chiffre faux. Sur nos huit photos réelles, aucune n'a été refusée ; sur des
            captures rendues plus difficiles, environ une sur sept l'est.
          </li>
          <li>
            Environ une capture rendue sur quarante donne un chiffre faux de plusieurs millimètres en
            passant les garde-fous classiques. Le troisième garde-fou, le désaccord avec le modèle
            entraîné, existe pour ça et s'active dès qu'un modèle est déposé.
          </li>
          <li>Verres teintés ou fortement traités : non testés, leur bord se comporte autrement.</li>
          <li>
            Alignement vertical des deux verres selon la convention « boxing » ; aucun axe pupillaire
            n'est mesuré.
          </li>
          <li>Pas de vraie rainure de clipsage dans la monture, seulement un jeu de 0,2 mm.</li>
          <li>
            Le repli sans imprimante (quatre coins placés à la main) est nettement moins précis.
          </li>
        </ul>
      </section>
    </div>
  )
}
